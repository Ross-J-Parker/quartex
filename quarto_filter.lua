-- quarto_filter.lua: Pandoc filter used by quartex.py.
-- Handles things Pandoc passes through unchanged, or in a form Quarto does not understand.

local env_names = {
  challenge_env = {kind = "note",      title = "Challenge"},
  problem_env   = {kind = "important", title = "Problem"},
  solution_env  = {kind = "tip",       title = "Worked solution"},
}

-- Shaded boxes from the source (e.g. \ebox, \derivation) become Quarto callouts. Quarto fixes each
-- callout type's colour: note = blue, warning = yellow, tip = green, caution = orange, important = red.
-- To change a heading, edit its title here. Any other box macro gets a note titled from its name.
local box_names = {
  ebox       = {kind = "note",    title = "Key point"},
  derivation = {kind = "warning", title = "Derivation"},
}

-- Layout-only wrappers that carry no meaning in HTML.
local unwrap_classes = {"center", "small", "titlepage"}

local function has_arabic(s)
  return s ~= nil and s:find("arabic", 1, true) ~= nil
end

-- True if an element contains a cross-reference or anchor built from an \arabic counter.
local function mentions_arabic(el)
  local found = false
  el:walk({
    Link = function(l)
      if has_arabic(l.target) then found = true end
    end,
    Span = function(s)
      if has_arabic(s.identifier) then found = true end
    end,
  })
  return found
end

-- Pass 1: remove paragraphs such as "(see Worked Solution ...)". They point at labels
-- built from \arabic counters, which cannot resolve in a static document.
-- Spans that only carry a \label add nothing in HTML, so they are unwrapped.
local function remove_counter_refs(doc)
  return doc:walk({
    Para = function(p)
      if mentions_arabic(p) then return {} end
    end,
    Span = function(s)
      if s.attributes["label"] ~= nil then return s.content end
    end,
  })
end

-- Pass 2: number callouts by chapter, unwrap layout divs, and replace the remaining
-- counter-based links (e.g. "Problem 2.2" in captions) with the chapter.number they mean.
local function number_and_link(doc)
  local chapter = 0
  local counts = {challenge_env = 0, problem_env = 0, solution_env = 0}
  -- Identifiers that exist in this document, so links to anything else can be recognised.
  known_ids = {}
  doc:walk({
    Header = function(h) if h.identifier ~= "" then known_ids[h.identifier] = true end end,
    Div = function(d) if d.identifier ~= "" then known_ids[d.identifier] = true end end,
    Span = function(s) if s.identifier ~= "" then known_ids[s.identifier] = true end end,
  })
  return doc:walk({
    Header = function(h)
      if h.level == 1 then
        chapter = chapter + 1
        for k in pairs(counts) do counts[k] = 0 end
      end
    end,
    Link = function(l)
      if has_arabic(l.target) then
        local n = l.target:match("_(%d+)$")
        if n then
          return pandoc.Str(chapter .. "." .. n)
        end
        return pandoc.Str("")
      end
      -- References to problems and solutions, e.g. \ref{problem:8_3} -> "8.3". These labels are
      -- not Quarto anchors, so the link is replaced with its number (or its text if unrecognised).
      local a, b = l.target:match("^#%a+:(%d+)_(%d+)$")
      if a then
        return pandoc.Str(a .. "." .. b)
      end
      if l.target:match("^#%a+:") and not l.target:match("^#(sec|fig|tbl|eq)-") then
        return l.content
      end
      -- Any other link to a label that does not exist in the document (e.g. \ref{DensityOperatorVonNeumann}
      -- with no prefix) cannot resolve. Typst stops on such links, so they become plain text.
      if l.target:sub(1, 1) == "#" and not l.target:match("^#(sec|fig|tbl|eq)-")
          and not known_ids[l.target:sub(2)] then
        return l.content
      end
    end,
    Div = function(d)
      for _, c in ipairs(d.classes) do
        if c:sub(1, 4) == "box_" then
          local name = c:sub(5)
          local info = box_names[name] or {kind = "note", title = (name:gsub("^%l", string.upper))}
          return pandoc.Div(d.content, pandoc.Attr("", {"callout-" .. info.kind}, {title = info.title}))
        end
      end
      for cls, info in pairs(env_names) do
        if d.classes:includes(cls) then
          counts[cls] = counts[cls] + 1
          local title = info.title .. " " .. chapter .. "." .. counts[cls]
          local attr = pandoc.Attr("", {"callout-" .. info.kind}, {title = title})
          return pandoc.Div(d.content, attr)
        end
      end
      for _, cls in ipairs(unwrap_classes) do
        if d.classes:includes(cls) then
          return d.content
        end
      end
    end,
  })
end

-- Pandoc gives images with no caption the alt text "image". Use the file name instead.
local function fix_alt(img)
  if pandoc.utils.stringify(img.caption) == "image" then
    local stem = img.src:match("([^/]+)%.%w+$") or "figure"
    img.caption = pandoc.Inlines({})
    img.attributes["fig-alt"] = (stem:gsub("[-_]", " "))
    return img
  end
end

-- Contents: Quarto's own "On this page" panel is used (set in the document header), so the
-- filter does not add a second contents list.

function Pandoc(doc)
  doc = remove_counter_refs(doc)
  doc = number_and_link(doc)
  doc = doc:walk({Image = fix_alt})
  return doc
end
