-- Confidential-review adapter. Python supplies only canonical manuscript
-- identity as metadata; this filter owns the notice wording and emits one
-- semantic page plus a LaTeX mode switch for PDF presentation.

local function meta_text(meta, key)
  local value = meta[key]
  return value and pandoc.utils.stringify(value) or ""
end

local function enabled(meta, key)
  local value = meta_text(meta, key):lower()
  return value == "true" or value == "yes" or value == "1"
end

local function inlines(text)
  local result = pandoc.Inlines({})
  for word in text:gmatch("%S+") do
    if #result > 0 then
      result:insert(pandoc.Space())
    end
    result:insert(pandoc.Str(word))
  end
  return result
end

local function latex_escape(text)
  local replacements = {
    ["\\"] = "\\textbackslash{}",
    ["{"] = "\\{",
    ["}"] = "\\}",
    ["%"] = "\\%",
    ["$"] = "\\$",
    ["#"] = "\\#",
    ["&"] = "\\&",
    ["_"] = "\\_",
    ["~"] = "\\textasciitilde{}",
    ["^"] = "\\textasciicircum{}",
  }
  return (text:gsub("[\\{}%%$#&_~%^]", function(character)
    return replacements[character]
  end))
end

function Pandoc(doc)
  if not enabled(doc.meta, "authorlm-review-copy") then
    return doc
  end

  local author = meta_text(doc.meta, "author")
  local owner = meta_text(doc.meta, "copyright-owner")
  local year = meta_text(doc.meta, "copyright-year")
  local notice = pandoc.Div({
    pandoc.Header(1, inlines(
      "CONFIDENTIAL PREPUBLICATION REVIEW DRAFT"),
      pandoc.Attr("", {"unnumbered"})),
    pandoc.Para(inlines("Author: " .. author)),
    pandoc.Para(inlines("© " .. year .. " " .. owner
      .. ". All rights reserved.")),
    pandoc.Para(inlines(
      "This unpublished draft is shared solely for private evaluation and "
      .. "feedback. Please do not reproduce, quote publicly, upload, post, "
      .. "forward, distribute, or adapt it without the author's prior written "
      .. "permission.")),
    pandoc.Para(inlines(
      "This is an independent work. References to people, titles, "
      .. "organizations, products, and other works are for identification, "
      .. "commentary, and discussion. No affiliation or endorsement is "
      .. "claimed.")),
    pandoc.Para(inlines(
      "This manuscript contains speculative philosophical, religious, and "
      .. "autobiographical reflections. It is not medical, psychological, "
      .. "legal, or other professional advice.")),
    pandoc.Para(inlines(
      "The author may consider voluntarily provided feedback when revising "
      .. "the manuscript. Feedback will not be attributed publicly without "
      .. "the reviewer's permission.")),
  }, pandoc.Attr("", {"authorlm-review-notice"}))

  table.insert(doc.blocks, 1,
    pandoc.RawBlock("latex", "\\AuthorLMReviewMode{" .. latex_escape(owner)
      .. "}{" .. latex_escape(year) .. "}"))
  table.insert(doc.blocks, 2, notice)
  table.insert(doc.blocks, 3,
    pandoc.RawBlock("latex", "\\AuthorLMReviewNoticeBreak{}"))
  return doc
end
