-- no-latex.lua
-- Drop any Div/Span with class "nolatex" when writing LaTeX/PDF.

local function is_latex()
  return FORMAT:match("latex")
end

function Div(el)
  if is_latex() and el.classes:includes("nolatex") then
    return {} -- remove the whole div (and its contents) in LaTeX/PDF
  end
  return nil -- keep as-is otherwise
end

function Span(el)
  if is_latex() and el.classes:includes("nolatex") then
    return {} -- same idea for inline spans
  end
  return nil
end
