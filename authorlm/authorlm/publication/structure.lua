-- Shared semantic adapter from AuthorLM's file wrappers to Pandoc writers.
-- Typography stays in pdf-header.tex and epub.css; this filter only assigns
-- roles and carries file boundaries to the writer that can express them.

local seen_file = false

local function has_class(element, wanted)
  for _, class in ipairs(element.classes) do
    if class == wanted then
      return true
    end
  end
  return false
end

function Div(div)
  if not has_class(div, "authorlm-file") then
    return nil
  end

  if seen_file then
    table.insert(div.content, 1,
      pandoc.RawBlock("latex", "\\AuthorLMEssayBreak{}"))
  end
  seen_file = true

  if has_class(div, "authorlm-title-page") then
    table.insert(div.content, 1,
      pandoc.RawBlock("latex", "\\thispagestyle{empty}"))
    for index, block in ipairs(div.content) do
      if block.t == "Header" and block.level == 1 then
        table.insert(block.classes, "authorlm-book-title")
        table.insert(block.content, 1,
          pandoc.RawInline("latex", "\\AuthorLMBookTitle{}"))
        div.content[index] = block
        break
      end
    end
  end

  return div
end
