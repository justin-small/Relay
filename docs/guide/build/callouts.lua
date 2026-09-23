-- Callout boxes (::: note / ::: tip / ::: warning) and part pages, for both
-- outputs from one source.
--
--   typst : wrapped in #callout("kind")[ ... ] from guide.typst
--   docx  : a bold label paragraph, then the body, all in the custom
--           paragraph style Note / Tip / Warning from reference.docx

local KINDS = { note = "Note", tip = "Tip", warning = "Warning" }

local function kind_of(div)
  for _, c in ipairs(div.classes) do
    if KINDS[c] then return c end
  end
end

function Div(div)
  local kind = kind_of(div)
  if not kind then return nil end
  if FORMAT:match("typst") then
    local out = { pandoc.RawBlock("typst", '#callout("' .. kind .. '")[') }
    for _, b in ipairs(div.content) do table.insert(out, b) end
    table.insert(out, pandoc.RawBlock("typst", "]"))
    return out
  elseif FORMAT:match("docx") then
    local label = pandoc.Para({ pandoc.Strong({ pandoc.Str(KINDS[kind]) }) })
    local blocks = { label }
    for _, b in ipairs(div.content) do table.insert(blocks, b) end
    -- An empty paragraph after the box, so two boxes in a row do not run
    -- together into one shaded block.
    return { pandoc.Div(blocks, pandoc.Attr("", {}, { ["custom-style"] = KINDS[kind] })),
             pandoc.RawBlock("openxml", "<w:p/>") }
  end
end

-- Part headings (# Part N: ... {.part}) start on a new page in Word as well.
-- Typst already breaks before every level-1 heading.
function Header(h)
  if FORMAT:match("docx") and h.level == 1 then
    local br = pandoc.RawBlock("openxml", '<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
    return { br, h }
  end
end

-- Word: the title block alone on page 1, then the contents on page 2. Pandoc's
-- own --toc would put the contents straight under the title, so the DOCX build
-- leaves it off and this adds the same TOC field after a page break. (The
-- page break before the first chapter heading, above, ends the contents page.)
local TOC = [[
<w:p><w:r><w:br w:type="page"/></w:r></w:p>
<w:sdt><w:sdtPr><w:docPartObj><w:docPartGallery w:val="Table of Contents"/><w:docPartUnique/></w:docPartObj></w:sdtPr>
<w:sdtContent>
<w:p><w:pPr><w:pStyle w:val="TOCHeading"/></w:pPr><w:r><w:t>Contents</w:t></w:r></w:p>
<w:p><w:r><w:fldChar w:fldCharType="begin" w:dirty="true"/><w:instrText xml:space="preserve"> TOC \o "1-2" \h \z \u </w:instrText><w:fldChar w:fldCharType="separate"/><w:t>Right-click here and choose Update Field to show the contents.</w:t><w:fldChar w:fldCharType="end"/></w:r></w:p>
</w:sdtContent></w:sdt>]]

function Pandoc(doc)
  if FORMAT:match("docx") then
    local m = doc.meta
    if m.version and m.date then
      m.date = pandoc.MetaInlines(pandoc.Inlines("Version " .. pandoc.utils.stringify(m.version)
        .. " · " .. pandoc.utils.stringify(m.date)))
    end
    table.insert(doc.blocks, 1, pandoc.RawBlock("openxml", TOC))
    return doc
  end
end
