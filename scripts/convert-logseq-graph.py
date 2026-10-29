#!/usr/bin/python3
"""Convert a file-based Logseq graph into a new SilverBullet space folder.

The graph is only read. The target must not exist yet; the script writes pages,
journals and assets there in SilverBullet's conventions, Logseq's templates as
slash templates and its simple queries as live queries, plus a `Logseq import`
page listing what needs a person's attention: queries it could not translate,
whiteboards, renamed pages and lines from Syncthing conflict copies.

Remove after 2027-01-20, with tests/test_logseq_conversion.py and
docs/MIGRATING-PASSWORDS-AND-NOTES.md.
"""
import argparse
import datetime
import json
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import unquote

REPORT = 'Logseq import'
LIBRARY = 'Library/Logseq'
TEMPLATES = 'Library/Templates/'
# Logseq-only properties with no meaning in SilverBullet; query-* ones shape the converted query.
DROPPED = {'collapsed', 'id', 'ls-type', 'hl-page', 'hl-color', 'hl-type', 'hl-stamp', 'filters', 'public',
           'heading', 'template', 'template-including-parent', 'exclude-in-graph', 'exclude_in_graph',
           'query-table', 'query-properties', 'query-sort-by', 'query-sort-desc'}
# Attributes SilverBullet sets itself on pages and on items; Logseq properties of these names are renamed.
PAGE_FIELDS = {'name', 'size', 'contentType', 'created', 'lastModified', 'perm', 'ref', 'tag', 'itags',
               'displayName', 'aliases'}
ITEM_FIELDS = {'ref', 'tag', 'tags', 'itags', 'pos', 'toPos', 'range', 'name', 'text', 'page',
               'pageLastModified', 'links', 'ilinks', 'parent', 'state', 'done'}
TASKS = {'TODO': '[ ]', 'LATER': '[ ]', 'DONE': '[x]', 'NOW': '[DOING]', 'DOING': '[DOING]',
         'WAIT': '[WAITING]', 'WAITING': '[WAITING]', 'IN-PROGRESS': '[DOING]',
         'CANCELED': '[CANCELED]', 'CANCELLED': '[CANCELED]'}
WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
          'September', 'October', 'November', 'December']
PROPERTY = re.compile(r'^([A-Za-z][\w-]*):: ?(.*)$')
TASK = re.compile(r'^(\s*- )(' + '|'.join(TASKS) + r') ')
FENCE = re.compile(r'^\s*(```|~~~)')
CODE_SPAN = re.compile(r'(`+)(.+?)\1')
LINK = re.compile(r'(#?)\[\[([^\[\]]+)\]\]')
ASSET = re.compile(r'(!?)\[([^\]]*)\]\((?:\.\./)+assets/([^)\s]+)\)(\{[^}]*\})?')
# Logseq tags may hold characters a SilverBullet hashtag cannot; those become links.
HASHTAG = re.compile(r'(?<![\w#\[/])#([^\s#\[\]()]+)')
NOT_A_TAG = re.compile(r'[!@#$%^&*(),.?":{}|<>\\]|^\d+$')
TAG_LINE = re.compile(r'^- ((?:#\[\[[^\]]+\]\]|#[^\s#\[]+)\s*)+$')
TAG = re.compile(r'#\[\[([^\]]+)\]\]|#([^\s#\[,]+)')
HEADING = re.compile(r'^(?:- )?#{1,6} (.+?)\s*$')
MACRO = re.compile(r'\{\{(\S+)\s*(.*?)\}\}')
PLANNING = re.compile(r'^(\s*)(SCHEDULED|DEADLINE): <(\d{4}-\d{2}-\d{2})[^>]*>\s*$')
JOURNAL_FILE = re.compile(r'^(\d{4})_(\d{2})_(\d{2})$')
JOURNAL_NAME = re.compile(r'^Journal/\d{4}-\d{2}-\d{2}$')
CONFLICT = re.compile(r'\.sync-conflict-\d{8}-\d{6}-[A-Z0-9]+')
YOUTUBE = re.compile(r'(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|embed/)|youtu\.be/)([\w-]{6,})')
BLOCK = re.compile(r'^(\t*)- ')

# Space Lua for the converted queries: Logseq treats a tag and a link to a page as the same
# reference, and its blocks are SilverBullet's items and tasks together.
LIBRARY_PAGE = '''---
tags: meta
---
Helpers for the queries converted from Logseq, where a tag and a link to a page are the same reference and a query searches every block, item or task.

```space-lua
-- priority: 10
logseq = logseq or {}

-- True when the object, or an item above it, tags or links the named page.
function logseq.refers(o, name)
  local wanted = string.lower(name)
  for _, list in ipairs({o.itags or {}, o.ilinks or {}}) do
    for _, value in ipairs(list) do
      if string.lower(value) == wanted then
        return true
      end
    end
  end
  return false
end

-- Logseq's task markers as SilverBullet task states.
local states = {TODO = " ", LATER = " ", NOW = "doing", DOING = "doing", DONE = "x", WAIT = "waiting",
  WAITING = "waiting", ["IN-PROGRESS"] = "doing", CANCELED = "canceled", CANCELLED = "canceled"}
function logseq.marker(o, marker)
  return o.tag == "task" and string.lower(o.state or "") == (states[marker] or string.lower(marker))
end

-- Logseq's blocks: every item and every task, except those on meta pages such as templates.
local function content(o)
  for _, tag in ipairs(o.itags or {}) do
    if tag == "meta" or string.startsWith(tag, "meta/") then
      return false
    end
  end
  return true
end
function logseq.blocks()
  local blocks = {}
  for _, source in ipairs({query[[from i = index.items()]], query[[from t = index.tasks()]]}) do
    for _, o in ipairs(source) do
      if content(o) then
        table.insert(blocks, o)
      end
    end
  end
  return blocks
end

-- A block as a list entry; tasks stay checkable.
function logseq.render(o)
  if o.tag == "task" then
    return templates.taskItem(o)
  end
  return templates.itemItem(o)
end
```
'''


def edn_string(config, key, default):
    match = re.search(r'^\s*:' + re.escape(key) + r'\s+"([^"]*)"', config, re.M)
    return match[1] if match else default


def title_pattern(fmt):
    """A regular expression for Logseq's journal title format, with named date parts."""
    tokens = {'EEEE': '(?:' + '|'.join(WEEKDAYS) + ')', 'EEE': '(?:' + '|'.join(d[:3] for d in WEEKDAYS) + ')',
              'MMMM': '(?P<monthname>' + '|'.join(MONTHS) + ')',
              'MMM': '(?P<monthabbr>' + '|'.join(m[:3] for m in MONTHS) + ')',
              'MM': r'(?P<month>\d{2})', 'M': r'(?P<month>\d{1,2})', 'yyyy': r'(?P<year>\d{4})',
              'do': r'(?P<day>\d{1,2})(?:st|nd|rd|th)', 'dd': r'(?P<day>\d{2})', 'd': r'(?P<day>\d{1,2})'}
    out, i = '', 0
    while i < len(fmt):
        for token in sorted(tokens, key=len, reverse=True):
            if fmt.startswith(token, i):
                out += tokens[token]
                i += len(token)
                break
        else:
            out += re.escape(fmt[i])
            i += 1
    return re.compile('^' + out + '$', re.I)


def journal_date(title, pattern):
    match = pattern.match(title)
    if not match:
        return None
    parts = match.groupdict()
    if parts.get('monthname'):
        month = [m.lower() for m in MONTHS].index(parts['monthname'].lower()) + 1
    elif parts.get('monthabbr'):
        month = [m[:3].lower() for m in MONTHS].index(parts['monthabbr'].lower()) + 1
    else:
        month = int(parts['month'])
    try:
        return datetime.date(int(parts['year']), month, int(parts['day']))
    except ValueError:
        return None


def file_page_name(stem, name_format):
    if name_format == 'triple-lowbar':
        stem = stem.replace('___', '/')
    else:  # the legacy format encoded namespaces with %2F or a single dot
        stem = stem.replace('.', '/')
    return unquote(stem) if re.search(r'%[0-9A-Fa-f]{2}', stem) else stem


def clean_name(name):
    """A valid SilverBullet name: no | @ #, no leading . ^ /, no empty or dot segments."""
    name = re.sub(r'[|#]', '-', name).replace('@', '')
    parts = [p.strip() for p in name.split('/')]
    parts = [p.lstrip('.^').strip() for p in parts if p.strip(' .')]
    name = '/'.join(p for p in parts if p)
    if name.lower().endswith('.md'):
        name = name[:-3]
    return name or 'Untitled'


def page_properties(lines):
    """Split a page's leading `key:: value` lines (bare or as the first bullet) from its body."""
    props, i = {}, 0
    bulleted = bool(lines) and lines[0].startswith('- ') and PROPERTY.match(lines[0][2:])
    while i < len(lines):
        line = lines[i]
        if bulleted:
            line = line[2:] if i == 0 or line.startswith('  ') else ''
        match = PROPERTY.match(line)
        if not match:
            break
        props[match[1].lower()] = match[2].strip()
        i += 1
    while i < len(lines) and lines[i].strip() in ('', '-'):
        i += 1
    return props, lines[i:]


def read_lines(path):
    return path.read_text(encoding='utf-8', errors='replace').replace('\r\n', '\n').split('\n')


def yaml_value(value):
    """A YAML scalar or flow list, quoted only where plain YAML would misread it."""
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, list):
        return '[' + ', '.join(yaml_value(v) for v in value) + ']'
    plain = (re.fullmatch(r'[^\W\d][\w ./-]*', value) and not value.endswith(' ')
             and value.lower() not in ('true', 'false', 'yes', 'no', 'on', 'off', 'null'))
    return value if plain else json.dumps(value, ensure_ascii=False)


def lua_string(value):
    return json.dumps(value, ensure_ascii=False)


def first_block(body, pattern):
    """The match and index of the page's first block when it fits pattern and has no children."""
    lines = [i for i, line in enumerate(body) if line.strip()]
    if not lines:
        return None, None
    first = lines[0]
    match = pattern.match(body[first].rstrip())
    child = len(lines) > 1 and body[lines[1]][:1] in ('\t', ' ')
    return (match, first) if match and not child else (None, None)


def split_refs(value):
    """The page names in a property value: [[links]], #tags or comma-separated words."""
    if '[[' in value or '#' in value:
        found = re.findall(r'#?\[\[([^\[\]]+)\]\]|#([^\s,#\[]+)', value)
        return [a or b for a, b in found]
    return [v.strip() for v in value.split(',') if v.strip()]


def hashtag(tag):
    return '#' + tag if not NOT_A_TAG.search(tag) and not re.search(r'\s', tag) else f'#<{tag}>'


def attribute_name(key, reserved):
    head, *rest = key.split('-')
    name = head + ''.join(p[:1].upper() + p[1:] for p in rest)
    return 'logseq' + name[:1].upper() + name[1:] if name in reserved else name


def block_end(lines, start):
    """The index after the block that starts at lines[start], with its properties and children."""
    depth = len(BLOCK.match(lines[start])[1])
    end = start + 1
    while end < len(lines):
        line = lines[end]
        tabs = len(line) - len(line.lstrip('\t'))
        if line.strip() and tabs <= depth and not line[tabs:].startswith('  '):
            break
        end += 1
    return end


def block_properties(lines, start):
    """The `key:: value` continuation lines right under a block's first line."""
    depth = len(BLOCK.match(lines[start])[1])
    props = {}
    for line in lines[start + 1:]:
        prefix = '\t' * depth + '  '
        match = PROPERTY.match(line[len(prefix):]) if line.startswith(prefix) else None
        if not match:
            break
        props[match[1].lower()] = match[2].strip()
    return props


class Converter:
    def __init__(self, graph, target):
        self.graph, self.target = graph, target
        config_path = graph / 'logseq/config.edn'
        config = config_path.read_text(encoding='utf-8', errors='replace') if config_path.exists() else ''
        self.title = title_pattern(edn_string(config, 'journal/page-title-format', 'MMM do, yyyy'))
        fmt = re.search(r':file/name-format\s+:([\w-]+)', config)
        self.name_format = fmt[1] if fmt else 'legacy'
        favorites = re.search(r':favorites\s+\[([^\]]*)\]', config)
        self.favorites = re.findall(r'"([^"]+)"', favorites[1]) if favorites else []
        self.names = {}      # lower-case Logseq name -> SilverBullet name
        self.pages = []      # (source, name, props, body lines)
        self.templates = {}  # SilverBullet template page name -> body lines in Logseq syntax
        self.report = {'renamed': [], 'queries': [], 'translated': [], 'macros': [], 'block refs': [],
                       'conflicts': [], 'empty journals': 0, 'redundant conflicts': 0, 'whiteboards': [],
                       'other assets': [], 'invalid assets': []}

    def name_for(self, logseq_name):
        key = logseq_name.strip().lower()
        if key in self.names:
            return self.names[key]
        when = journal_date(logseq_name.strip(), self.title)
        if when:
            return 'Journal/' + when.isoformat()
        return clean_name(logseq_name.strip())

    def collect(self):
        taken = set()

        def claim(name):
            base, n = name, 2
            while name.lower() in taken:
                name, n = f'{base} ({n})', n + 1
            taken.add(name.lower())
            return name

        copies = {}
        for folder in ('journals', 'pages'):
            for source in sorted((self.graph / folder).glob('*.md')):
                original = source.with_name(CONFLICT.sub('', source.name))
                if original != source and original.exists():
                    copies.setdefault(original, []).append(source)
                    continue
                lines = read_lines(source)
                props, body = page_properties(lines)
                journal = JOURNAL_FILE.match(original.stem) if folder == 'journals' else None
                if journal:
                    if not any(line.strip() not in ('', '-') for line in body) and not props:
                        self.report['empty journals'] += 1
                        continue
                    logseq = None
                    name = 'Journal/' + '-'.join(journal.groups())
                else:
                    logseq = props.get('title') or file_page_name(original.stem, self.name_format)
                    name = clean_name(logseq)
                name = claim(name)
                if logseq and name != logseq:
                    self.report['renamed'].append((logseq, name))
                if logseq:
                    self.names[logseq.lower()] = name
                for alias in split_refs(props.get('alias', '')):
                    self.names.setdefault(alias.lower(), name)
                self.pages.append((source, name, props, body))
        # A Syncthing conflict copy is an older or parallel version of its page:
        # only its lines that the page lacks are kept, in the report.
        names = {source: name for source, name, _, _ in self.pages}
        for original, sources in copies.items():
            known = {line.strip() for line in read_lines(original)}
            extra = []
            for source in sources:
                for line in read_lines(source):
                    if line.strip() not in known and line.strip() not in ('', '-'):
                        known.add(line.strip())
                        extra.append(line.rstrip())
            if extra and original in names:
                self.report['conflicts'].append((names[original], extra))
            else:
                self.report['redundant conflicts'] += len(sources)

    def inline(self, text, page, query=None):
        def link(match):
            return f'[[{self.name_for(match[2])}]]'

        def tag(match):
            name = match[1]
            trail = re.search(r'[,.;:!?"]+$', name)
            name, rest = (name[:trail.start()], trail[0]) if trail else (name, '')
            if name and NOT_A_TAG.search(name):
                return f'[[{self.name_for(name)}]]' + rest
            return match[0]

        def asset(match):
            path = unquote(match[3])
            size = ''
            if match[4]:
                width = re.search(r':width\s+(\d+)', match[4])
                height = re.search(r':height\s+(\d+)', match[4])
                size = '|' + (width[1] if width else '') + (f'x{height[1]}' if height else '')
            if match[1]:
                return f'![[assets/{path}{size}]]'
            return f'[[assets/{path}|{match[2] or path}]]'

        def macro(match):
            kind, arg = match[1], match[2].strip()
            if kind in ('video', 'youtube', 'vimeo'):
                youtube = YOUTUBE.search(arg)
                if youtube:
                    return '${embed.youtube "https://youtu.be/' + youtube[1] + '"}'
                if re.match(r'https://(?:www\.)?vimeo\.com/\d+', arg):
                    return '${embed.vimeo ' + lua_string(arg) + '}'
                return arg
            if kind in ('tweet', 'twitter'):
                return arg
            if kind == 'zotero-imported-file':
                key, _, title = arg.partition(',')
                title = title.strip().strip('"') or key
                return f'[{title}](zotero://open-pdf/library/items/{key.strip()})'
            if kind == 'query':
                translated = self.query(arg, query or {})
                if translated:
                    self.report['translated'].append(page)
                    return translated
                self.report['queries'].append(page)
            else:
                self.report['macros'].append(page)
            return '`' + match[0] + '`'

        # Code spans stay as written, and converted macros and assets are not read again as links or tags.
        pieces, last = [], 0
        for span in CODE_SPAN.finditer(text):
            pieces.append(('text', text[last:span.start()]))
            pieces.append(('code', span[0]))
            last = span.end()
        pieces.append(('text', text[last:]))
        split = []
        for kind, piece in pieces:
            if kind == 'code':
                split.append((kind, piece))
                continue
            last = 0
            for found in re.finditer(f'{MACRO.pattern}|{ASSET.pattern}', piece):
                split.append(('text', piece[last:found.start()]))
                converted = MACRO.match(found[0])
                split.append(('code', macro(converted) if converted else asset(ASSET.match(found[0]))))
                last = found.end()
            split.append(('text', piece[last:]))
        out = []
        for kind, piece in split:
            if kind == 'text':
                piece = LINK.sub(link, piece)
                piece = HASHTAG.sub(tag, piece)
                if re.search(r'\(\([0-9a-f-]{36}\)\)', piece):
                    self.report['block refs'].append(page)
            out.append(piece)
        return ''.join(out)

    def query(self, expression, props):
        """A Logseq simple query of references, task markers, and, or and not, as a SilverBullet query."""
        tokens = re.findall(r'\(|\)|#?\[\[[^\]]+\]\]|"[^"]*"|[^\s()]+', expression)

        def parse(position):
            if tokens[position] != '(':
                return tokens[position], position + 1
            node, position = [], position + 1
            while position < len(tokens) and tokens[position] != ')':
                child, position = parse(position)
                node.append(child)
            return node, position + 1

        def condition(node):
            if isinstance(node, list):
                if not node or not isinstance(node[0], str):
                    return None
                op, args = node[0].lower(), node[1:]
                if op == 'task':
                    markers = [a.upper() for a in args if isinstance(a, str) and a.upper() in TASKS]
                    return '(' + ' or '.join(f'logseq.marker(o, {lua_string(m)})' for m in markers) + ')' if markers else None
                parts = [condition(a) for a in args]
                if not parts or None in parts:
                    return None
                if op in ('and', 'or'):
                    return '(' + f' {op} '.join(parts) + ')'
                if op == 'not':
                    return 'not (' + ' or '.join(parts) + ')'
                return None
            ref = re.fullmatch(r'#?\[\[([^\]]+)\]\]|#([^\s]+)', node)
            if not ref:
                return None
            name = ref[1] or ref[2]
            if name.upper() in TASKS:
                return f'logseq.marker(o, {lua_string(name.upper())})'
            return f'logseq.refers(o, {lua_string(self.name_for(name))})'

        try:
            tree, position = parse(0)
        except (IndexError, RecursionError):
            return None
        where = condition(tree) if tokens and position == len(tokens) else None
        if not where:
            return None
        clauses = [f'from o = logseq.blocks()', f'where {where}']
        sort = props.get('query-sort-by', '').strip()
        if sort and sort not in ('block', 'page'):
            order = 'desc' if props.get('query-sort-desc', '').strip() == 'true' else 'asc'
            clauses.append(f'order by o.{attribute_name(sort.lower(), ITEM_FIELDS)} {order} nulls last')
        columns = re.findall(r':([\w-]+)', props.get('query-properties', ''))
        if props.get('query-table', '').strip() == 'true' and columns and 'logseq.marker' not in where:
            fields = ['page = "[[" .. o.page .. "]]"']
            for column in columns:
                if column in ('tag', 'tags'):
                    fields.append('tags = table.concat(o.tags or {}, " ")')
                elif column == 'block':
                    fields.append('block = o.name')
                elif column != 'page':
                    key = attribute_name(column.lower(), ITEM_FIELDS)
                    fields.append(f'{key} = o.{key}')
            clauses.append('select {' + ', '.join(fields) + '}')
        else:
            clauses.append('select logseq.render(o)')
        return '${query[[' + ' '.join(clauses) + ']]}'

    def extract_templates(self, name, body):
        """Logseq template blocks become slash templates; the page keeps a link in their place."""
        out, i = [], 0
        while i < len(body):
            if not BLOCK.match(body[i]):
                out.append(body[i])
                i += 1
                continue
            props = block_properties(body, i)
            end = block_end(body, i)
            template = props.get('template', '').strip()
            if not template:
                out.append(body[i])
                i += 1
                continue
            depth = len(BLOCK.match(body[i])[1])
            include_parent = props.get('template-including-parent', 'true').strip() != 'false'
            block = [line[depth:] if line.startswith('\t' * depth) else line for line in body[i:end]]
            if not include_parent:
                block = [line[1:] if line.startswith('\t') else line for line in block[1:]
                         if not (line.startswith('  ') and PROPERTY.match(line[2:]))]
            page = TEMPLATES + clean_name(template)
            self.templates[page] = (template, block)
            out.append('\t' * depth + f'- Slash template /{clean_name(template)}: [[{page}]]')
            i = end
        return out

    def body(self, lines, page):
        out, fenced, logbook = [], False, False
        for index, line in enumerate(lines):
            if FENCE.match(line):
                fenced = not fenced
            if fenced or FENCE.match(line):
                out.append(line)
                continue
            stripped = line.strip()
            if stripped == ':LOGBOOK:':
                logbook = True
                continue
            if logbook:
                logbook = stripped != ':END:'
                continue
            query = block_properties(lines, index) if BLOCK.match(line) and '{{query' in line else None
            indent = len(line) - len(line.lstrip('\t'))
            line = '  ' * indent + line[indent:]
            planning = PLANNING.match(line)
            if planning:
                out.append(f'{planning[1]}[{planning[2].lower()}: {planning[3]}]')
                continue
            # A property line inside a block, or a bullet that holds only a property.
            bullet = '- ' if stripped.startswith('- ') else ''
            prop = PROPERTY.match(stripped[len(bullet):])
            if prop:
                key = prop[1].lower()
                if key in DROPPED:
                    continue
                lead = line[:len(line) - len(line.lstrip())] + bullet
                if key in ('tag', 'tags'):
                    tags = [self.name_for(t) if '[[' in prop[2] else t for t in split_refs(prop[2])]
                    out.append(lead + ' '.join(hashtag(t) for t in tags))
                    continue
                value = self.inline(prop[2].strip(), page)
                value = value if re.fullmatch(r'[\w .,/-]*', value) else yaml_value(value)
                out.append(lead + f'[{attribute_name(key, ITEM_FIELDS)}: {value}]')
                continue
            line = TASK.sub(lambda m: m[1] + TASKS[m[2]] + ' ', line)
            out.append(self.inline(line, page, query))
        while out and out[-1].strip() in ('', '-'):
            out.pop()
        return out

    def frontmatter(self, name, props, tags):
        """Page attributes in frontmatter, as SilverBullet's journal template and best practices keep them."""
        fields = {}
        if JOURNAL_NAME.match(name):
            fields['tags'] = ['journal']
            fields['date'] = datetime.date.fromisoformat(name.removeprefix('Journal/'))
        title = props.get('title')
        if title and clean_name(title) != title:
            fields['displayName'] = title
        for key, value in props.items():
            if key == 'title' or key in DROPPED:
                continue
            if key in ('tags', 'tag', 'alias'):
                field = 'aliases' if key == 'alias' else 'tags'
                values = [clean_name(v) if key == 'alias' else v for v in split_refs(value)]
                fields[field] = fields.get(field, []) + [v for v in values if v not in fields.get(field, [])]
                continue
            field = attribute_name(key, PAGE_FIELDS)
            fields[field if field not in fields else attribute_name(field, {field})] = self.inline(value, name)
        if tags:
            fields['tags'] = fields.get('tags', []) + [t for t in tags if t not in fields.get('tags', [])]
        return ['---', *(f'{key}: {yaml_value(value)}' for key, value in fields.items()), '---'] if fields else []

    def page_tags(self, name, body):
        """Hashtags in a page's childless first block tag the page, so they move to frontmatter."""
        if name.startswith('Journal/'):
            return [], body
        match, index = first_block(body, TAG_LINE)
        if not match:
            return [], body
        tags = [a or b for a, b in TAG.findall(body[index])]
        return tags, body[:index] + body[index + 1:]

    def without_title(self, name, logseq, body):
        """The page name is its title: a first heading that only restates it goes."""
        match, index = first_block(body, HEADING)
        titles = {name.lower(), name.rsplit('/', 1)[-1].lower(), (logseq or '').lower()}
        if match and match[1].strip().lower() in titles:
            return body[:index] + body[index + 1:]
        return body

    def write_page(self, name, lines):
        path = self.target / (name + '.md')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('\n'.join(lines).rstrip('\n') + '\n', encoding='utf-8')

    def write(self):
        for source, name, props, body in self.pages:
            tags, body = self.page_tags(name, body)
            body = self.without_title(name, props.get('title') or source.stem, body)
            body = self.extract_templates(name, body)
            self.write_page(name, self.frontmatter(name, props, tags) + self.body(body, name))
        for page, (template, block) in self.templates.items():
            front = ['---', 'tags: meta/template/slash',
                     f'description: {yaml_value("Logseq template " + template)}', '---']
            self.write_page(page, front + self.body(block, page))
        if self.report['translated']:
            self.write_page(LIBRARY, LIBRARY_PAGE.rstrip('\n').split('\n'))
        assets = self.graph / 'assets'
        if assets.is_dir():
            for source in sorted(p for p in assets.rglob('*') if p.is_file()):
                relative = source.relative_to(assets)
                if any(part.startswith('.') for part in relative.parts):
                    continue
                if re.search(r'[|@#]', str(relative)):
                    self.report['invalid assets'].append(str(relative))
                destination = self.target / 'assets' / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                if source.suffix == '.edn':
                    self.report['other assets'].append(str(source.relative_to(self.graph)))
        for board in sorted((self.graph / 'whiteboards').glob('*')) if (self.graph / 'whiteboards').is_dir() else []:
            self.report['whiteboards'].append(board.name)
        self.write_report()

    def write_report(self):
        r = self.report
        lines = ['---', 'tags: meta/logseq-import', '---',
                 f'Converted from the Logseq graph on {datetime.date.today().isoformat()}: '
                 f'{len(self.pages)} pages; {r["empty journals"]} empty journals and '
                 f'{r["redundant conflicts"]} conflict copies with nothing new left out.', '']

        def section(title, items, intro):
            if items:
                lines.extend([f'# {title}', intro, *items, ''])

        section('Queries to rewrite', [f'* [[{p}]]' for p in sorted(set(r['queries']))],
                'These Logseq queries are kept as code; rewrite them as Space Lua queries.')
        section('Converted queries', [f'* [[{p}]]' for p in sorted(set(r['translated']))],
                f'These Logseq queries now run as SilverBullet queries, with the helpers on [[{LIBRARY}]]; '
                'check that each finds what it found in Logseq.')
        section('Templates', [f'* [[{page}]]: `/{page.removeprefix(TEMPLATES)}`' for page in self.templates],
                'Logseq templates are now slash templates; type the command in a page to insert one.')
        section('Favourites', [f'* [[{self.name_for(f)}]]' for f in self.favorites],
                'Logseq\'s favourites; add the ones you want to the index page.')
        section('Other macros', [f'* [[{p}]]' for p in sorted(set(r['macros']))],
                'These pages hold a Logseq macro without a SilverBullet equivalent, kept as code.')
        section('Block references', [f'* [[{p}]]' for p in sorted(set(r['block refs']))],
                'These pages refer to Logseq blocks by id; give the block an $anchor and link it as [[$anchor]].')
        if r['conflicts']:
            lines.extend(['# Syncthing conflict copies',
                          'Lines that only Syncthing conflict copies of these pages hold, as Logseq wrote them. '
                          'Copy what you want to keep into the page.', ''])
            for page, extra in r['conflicts']:
                lines.extend([f'## [[{page}]]', '```', *extra, '```', ''])
        section('Whiteboards', [f'* `{w}`' for w in r['whiteboards']] + [f'* `{a}`' for a in r['other assets']],
                'Not converted. Open them in Logseq and export what you need as images.')
        section('Asset names', [f'* `assets/{a}`' for a in r['invalid assets']],
                'SilverBullet cannot open a file whose name contains | @ or #; rename it and its links.')
        section('Renamed pages', [f'* `{old}` → [[{new}]]' for old, new in r['renamed']],
                'SilverBullet names cannot contain | @ # or start with . ^ /; links follow the new names.')
        self.write_page(REPORT, lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('graph', type=Path, help='the Logseq graph folder, with pages/ and journals/')
    parser.add_argument('target', type=Path, help='a new folder for the SilverBullet space')
    args = parser.parse_args()
    if not (args.graph / 'pages').is_dir() and not (args.graph / 'journals').is_dir():
        raise SystemExit(f'{args.graph} is not a Logseq graph: no pages/ or journals/')
    if args.target.exists():
        raise SystemExit(f'{args.target} exists; name a new folder')
    converter = Converter(args.graph.resolve(), args.target)
    converter.collect()
    args.target.mkdir(parents=True)
    converter.write()
    r = converter.report
    print(f'{len(converter.pages)} pages written to {args.target}; {r["empty journals"]} empty journals left out')
    print(f'Review the "{REPORT}" page: {len(set(r["translated"]))} pages with converted queries, '
          f'{len(set(r["queries"]))} with queries to rewrite, {len(converter.templates)} templates, '
          f'{len(r["whiteboards"])} whiteboards, {len(r["renamed"])} renamed, '
          f'{len(r["conflicts"])} pages with lines from conflict copies')


if __name__ == '__main__':
    sys.exit(main())
