"""The Red Hat CoP Ansible practices that ansible-lint does not check (STYLE.md): values looked
up with brackets, internal variables named `__<role>_`, a bare variable in a condition cast with
`| bool`, and task files named in snake_case."""

from pathlib import Path
import re
import subprocess
import unittest

import yaml

try:
    import jinja2
    from jinja2.lexer import operators
except ImportError:  # Jinja is no prerequisite of Shaffy OS; CI installs python-jinja
    jinja2 = None

ROOT = Path(__file__).resolve().parents[1]
CONDITIONS = {'when', 'changed_when', 'failed_when', 'until', 'that'}
# The play's shared settings, which the operation and settings roles define for every role
# (.ansible-lint-ignore).
SHARED = {'shaffy', 'shaffy_settings_file'}
ENV = jinja2.Environment() if jinja2 else None


def playbooks():
    listed = subprocess.run(
        ['git', '-C', str(ROOT), 'ls-files', 'roles/*.yml', 'roles/*.j2', 'site.yml', 'records.yml'],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return [path for path in listed if (ROOT / path).is_file()]


def scalars(node, key=None):
    """Each string in a YAML node, with the key it belongs to."""
    if isinstance(node, yaml.MappingNode):
        for k, v in node.value:
            yield from scalars(v, k.value)
    elif isinstance(node, yaml.SequenceNode):
        for item in node.value:
            yield from scalars(item, key if key in CONDITIONS else None)
    elif isinstance(node, yaml.ScalarNode):
        yield key, node.value


def jinja(path):
    """The Jinja source in a file: its templates, and its bare conditions wrapped as one."""
    text = (ROOT / path).read_text()
    if path.endswith('.j2'):
        yield text
        return
    for key, value in scalars(yaml.compose(text)):
        if key in CONDITIONS and '{{' not in value:
            yield '{{ ' + value + ' }}'
        elif '{{' in value or '{%' in value:
            yield value


def dotted(source):
    """The `a.b` lookups in a template, leaving method calls and qualified filters and tests."""
    tokens = [(operators.get(value, kind) if kind == 'operator' else kind, value) for _, kind, value in ENV.lex(source)]
    found, i, n = [], 0, len(tokens)
    while i < n:
        kind, value = tokens[i]
        i += 1
        if kind == 'pipe' or (kind, value) == ('name', 'is'):
            while i < n and (tokens[i][0] == 'whitespace' or tokens[i] == ('name', 'not')):
                i += 1
            while i < n and tokens[i][0] in ('name', 'dot'):
                i += 1
        elif kind == 'dot' and 1 < i < n and tokens[i - 2][0] in ('name', 'rparen', 'rbracket'):
            after = next((t for t in tokens[i + 1 :] if t[0] != 'whitespace'), (None, None))
            if tokens[i][0] == 'name' and after[0] != 'lparen':
                found.append(f'{tokens[i - 2][1]}.{tokens[i][1]}')
    return found


@unittest.skipUnless(jinja2, 'needs python-jinja')
class AnsibleStyleTests(unittest.TestCase):
    def test_values_are_looked_up_with_brackets(self):
        for path in playbooks():
            for source in jinja(path):
                with self.subTest(path=path):
                    self.assertEqual(dotted(source), [], "write a['b'], not a.b")

    def test_the_lookup_check_tells_a_lookup_from_a_call_or_a_filter(self):
        self.assertEqual(dotted("{{ a.b['c'].d }}"), ['a.b', '].d'])
        self.assertEqual(dotted('{{ (x | from_json).y }}'), [').y'])
        self.assertEqual(dotted("{{ a['b'].split()[0] | community.general.json_query(q) }}"), [])
        self.assertEqual(dotted("{{ a is ansible.builtin.version('1.0', '>=') }}"), [])
        self.assertEqual(dotted("{{ lookup('ansible.builtin.env', 'X') }}"), [])
        self.assertEqual(dotted('{{ a is defined and b.c }}'), ['b.c'])

    def test_internal_variables_carry_two_underscores_and_the_role_name(self):
        for path in playbooks():
            parts = Path(path).parts
            if parts[0] != 'roles' or path.endswith('.j2') or 'meta' in parts:
                continue
            role = parts[1]
            data = yaml.safe_load((ROOT / path).read_text()) or []
            names = list(data) if 'vars' in parts else []
            for task in self.tasks(data if isinstance(data, list) else []):
                names += [task['register']] if 'register' in task else []
                names += list(task.get('ansible.builtin.set_fact', {}))
                names += list(task.get('vars', {}))
            for name in set(names) - SHARED:
                with self.subTest(path=path):
                    self.assertTrue(name.startswith(f'__{role}_'), name)

    def test_a_bare_variable_in_a_condition_is_cast(self):
        bare = re.compile(r'^(not\s+)?[a-z_][a-z0-9_]*$')
        for path in playbooks():
            if path.endswith('.j2'):
                continue
            for key, value in scalars(yaml.compose((ROOT / path).read_text())):
                if key in CONDITIONS and value not in ('true', 'false'):
                    with self.subTest(path=path, condition=value):
                        self.assertNotRegex(value.strip(), bare, 'add | bool')

    def test_a_condition_that_joins_several_with_and_is_a_list(self):
        for path in playbooks():
            if path.endswith('.j2'):
                continue
            for key, value in scalars(yaml.compose((ROOT / path).read_text())):
                if key in CONDITIONS - {'that'} and value not in ('true', 'false'):
                    output = ENV.parse('{{ ' + value + ' }}').body[0].nodes[0]
                    with self.subTest(path=path, condition=value):
                        self.assertNotIsInstance(output, jinja2.nodes.And, 'write the parts as a list')

    def test_task_files_are_named_in_snake_case(self):
        for path in playbooks():
            if '/tasks/' in path or '/handlers/' in path:
                self.assertRegex(Path(path).stem, r'^[a-z][a-z0-9_]*$', path)

    def tasks(self, items):
        for item in items:
            if isinstance(item, dict):
                yield item
                for nested in ('block', 'rescue', 'always'):
                    yield from self.tasks(item.get(nested, []))


if __name__ == '__main__':
    unittest.main()
