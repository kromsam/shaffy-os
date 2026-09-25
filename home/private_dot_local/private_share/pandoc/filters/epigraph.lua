-- pandoc-epigraph.lua
--
-- Epigraph filter with DOCX paragraph styles applied separately
-- to the quote ("Epigraph Text") and the source ("Epigraph Source").

local FORCE_ODD_PAGE_SECTION_BREAK_IN_DOCX = false

-- Helper: blocks → inlines (used for LaTeX)
local function blocks_to_inlines(block)
  if block.t == 'Para' or block.t == 'Plain' then
    return block.content
  end
  return pandoc.utils.blocks_to_inlines(block.content)
end

-- LaTeX/PDF handler (unchanged behavior)
local function handle_latex(div, quote, source)
  local width       = div.attributes.width or '.75'
  local pagestyle   = div.attributes.pagestyle or 'empty'
  local after       = div.attributes.after or ''
  local use_oddpage = (div.attributes.oddpage or 'true'):lower() ~= 'false'

  local quote_inl   = blocks_to_inlines(quote)
  local source_inl  = blocks_to_inlines(source)
  local quote_tex   = pandoc.write(pandoc.Pandoc(pandoc.Para(quote_inl)), 'latex'):gsub('%s*$', '')
  local source_tex  = pandoc.write(pandoc.Pandoc(pandoc.Para(source_inl)), 'latex'):gsub('%s*$', '')

  local parts       = {}
  if use_oddpage then table.insert(parts, '\\cleardoublepage') end
  if pagestyle ~= '' then table.insert(parts, '\\thispagestyle{' .. pagestyle .. '}') end
  table.insert(parts, '\\setlength{\\epigraphwidth}{' .. width .. '\\textwidth}')
  table.insert(parts, '\\epigraph{' .. quote_tex .. '}{' .. source_tex .. '}')
  if after == 'clear' then table.insert(parts, '\\clearpage') end

  return pandoc.RawBlock('latex', table.concat(parts, '\n'))
end

local function docx_pagebreak()
  return pandoc.RawBlock(
    'openxml',
    '<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:r><w:br w:type="page"/></w:r></w:p>'
  )
end

local function block_content(block)
  if block.t == 'BlockQuote' then
    return block.content
  else
    return { block }
  end
end

-- DOCX: optional page break + two styled blocks + optional page break
local function handle_docx(div, quote, source)
  local out = {}

  -- Page/section break first
  local use_odd_page_break = FORCE_ODD_PAGE_SECTION_BREAK_IN_DOCX
      and (div.attributes.oddpage or 'true'):lower() ~= 'false'

  local pagebreak_before = (div.attributes['pagebreak-before'] or 'true'):lower() ~= 'false'
  local pagebreak_after = (div.attributes['pagebreak-after'] or 'true'):lower() ~= 'false'

  if pagebreak_before and use_odd_page_break then
    local odd_break = table.concat({
      '<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">',
      '  <w:pPr><w:sectPr><w:type w:val="oddPage"/></w:sectPr></w:pPr>',
      '</w:p>'
    }, '\n')
    table.insert(out, pandoc.RawBlock('openxml', odd_break))
  elseif pagebreak_before then
    table.insert(out, docx_pagebreak())
  end

  -- Wrap quote paragraphs in a Div with custom-style="Epigraph Text"
  local quoteDiv = pandoc.Div(
    block_content(quote),
    pandoc.Attr('', { 'epigraph-quote' }, { ['custom-style'] = 'Epigraph Text' })
  )

  -- Wrap source paragraphs in a Div with custom-style="Epigraph Source"
  local sourceDiv = pandoc.Div(
    block_content(source),
    pandoc.Attr('', { 'epigraph-source' }, { ['custom-style'] = 'Epigraph Source' })
  )

  table.insert(out, quoteDiv)
  table.insert(out, sourceDiv)
  if pagebreak_after then table.insert(out, docx_pagebreak()) end

  return out
end

-- HTML/EPUB handler (unchanged behavior)
local function handle_html(div)
  local elements = {}
  local use_recto = (div.attributes.oddpage or 'true'):lower() ~= 'false'
  if use_recto then
    local recto_hint = '<div style="break-before: recto; page-break-before: right;"></div>'
    table.insert(elements, pandoc.RawBlock('html', recto_hint))
  end
  table.insert(elements, div)
  return elements
end

-- Main filter
function Div(div)
  if not div.classes:includes('epigraph') then return nil end
  if #div.content ~= 2 then return nil end

  local quote  = div.content[1]
  local source = div.content[2]

  if FORMAT:match('latex') then
    return handle_latex(div, quote, source)
  elseif FORMAT:match('docx') then
    return handle_docx(div, quote, source)
  elseif FORMAT:match('html') or FORMAT:match('epub') then
    return handle_html(div)
  else
    return div
  end
end
