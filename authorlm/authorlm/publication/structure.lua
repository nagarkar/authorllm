-- Shared semantic adapter from AuthorLM's file wrappers to Pandoc writers.
-- Typography stays in pdf-header.tex and epub.css; this filter only assigns
-- roles and carries file boundaries to the writer that can express them.

local seen_file = false
local matter_stage = 0

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

  -- Matter is monotonic — front, then main, then back — and the hook
  -- fires when the book first ADVANCES to a matter. A front-matter file
  -- filed after the main matter has begun (SMSTTD's preface sits under
  -- chapter 1) must not fire \frontmatter again: that would send the
  -- folios back to roman for the rest of the book.
  local order = {front = 1, main = 2, back = 3}
  local hooks = {"\\AuthorLMFrontMatter{}", "\\AuthorLMMainMatter{}",
                 "\\AuthorLMBackMatter{}"}
  for matter, rank in pairs(order) do
    if has_class(div, "authorlm-matter-" .. matter) and rank > matter_stage then
      matter_stage = rank
      table.insert(div.content, 1, pandoc.RawBlock("latex", hooks[rank]))
    end
  end

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
