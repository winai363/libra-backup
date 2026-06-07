-- fix-tables.lua: choose safer PDF table widths.
-- Pandoc's auto widths can make wide KDP tables unreadable on a 6x9 page.
-- Use content-aware widths instead of forcing every column to the same minimum.

local MIN_WIDTH = 0.08
local MAX_WIDTH = 0.26

local function clamp(value, min_value, max_value)
  if value < min_value then return min_value end
  if value > max_value then return max_value end
  return value
end

local function cell_text(cell)
  if not cell then return "" end
  local value = cell
  if cell.content then
    value = cell.content
  elseif cell.contents then
    value = cell.contents
  end
  return pandoc.utils.stringify(value):gsub("%s+", " ")
end

local function add_row_lengths(widths, row)
  if not row or not row.cells then return end
  for i, cell in ipairs(row.cells) do
    local text = cell_text(cell)
    local longest = 0
    for word in text:gmatch("%S+") do
      longest = math.max(longest, #word)
    end
    widths[i] = math.max(widths[i] or 0, #text, longest * 1.35)
  end
end

local HEADER_BREAKS = {
  ["Amortizaciones"] = {"Amortiza", "ciones"},
  ["Amortización"] = {"Amortiza", "ción"},
  ["Observaciones"] = {"Observa", "ciones"},
}

local function rewrite_header_cell(cell)
  local blocks = cell.content or cell.contents
  if not blocks then return cell end

  for _, block in ipairs(blocks) do
    if block.t == "Plain" or block.t == "Para" then
      local rewritten = pandoc.Inlines({})
      for _, inline in ipairs(block.content) do
        if inline.t == "Str" and HEADER_BREAKS[inline.text] then
          local parts = HEADER_BREAKS[inline.text]
          rewritten:insert(pandoc.Str(parts[1]))
          rewritten:insert(pandoc.LineBreak())
          rewritten:insert(pandoc.Str(parts[2]))
        else
          rewritten:insert(inline)
        end
      end
      block.content = rewritten
    end
  end

  return cell
end

local function rewrite_header_row(row)
  if not row or not row.cells then return end
  for i, cell in ipairs(row.cells) do
    row.cells[i] = rewrite_header_cell(cell)
  end
end

function Table(el)
  local n = #el.colspecs
  if n == 0 then return el end

  local widths = {}
  for i = 1, n do widths[i] = 0 end

  if el.head and el.head.rows then
    for _, row in ipairs(el.head.rows) do
      rewrite_header_row(row)
      add_row_lengths(widths, row)
    end
  end

  if el.bodies then
    for _, body in ipairs(el.bodies) do
      if body.body then
        for _, row in ipairs(body.body) do
          add_row_lengths(widths, row)
        end
      end
    end
  end

  local sum = 0
  for i = 1, n do
    local existing = el.colspecs[i][2] or 0
    if type(existing) ~= "number" then existing = 0 end
    widths[i] = math.max(widths[i], existing * 80, 8)
    sum = sum + widths[i]
  end

  for i = 1, n do
    widths[i] = widths[i] / sum
  end

  -- A hard 15% minimum breaks 6-7 column tables because the minimums add up
  -- past the page width. Use a smaller dynamic floor for wide tables.
  local floor = math.min(MIN_WIDTH, 0.92 / n)
  local changed = true
  while changed do
    changed = false
    local fixed = 0
    local flexible_sum = 0
    for i = 1, n do
      if widths[i] < floor then
        widths[i] = floor
        fixed = fixed + floor
        changed = true
      elseif widths[i] > MAX_WIDTH and n > 4 then
        widths[i] = MAX_WIDTH
        fixed = fixed + MAX_WIDTH
        changed = true
      else
        flexible_sum = flexible_sum + widths[i]
      end
    end
    if changed and flexible_sum > 0 and fixed < 1 then
      local scale = (1 - fixed) / flexible_sum
      for i = 1, n do
        if widths[i] > floor and (n <= 4 or widths[i] < MAX_WIDTH) then
          widths[i] = widths[i] * scale
        end
      end
    end
  end

  sum = 0
  for i = 1, n do
    widths[i] = clamp(widths[i], floor, 1)
    sum = sum + widths[i]
  end
  for i = 1, n do widths[i] = widths[i] / sum end

  for i, spec in ipairs(el.colspecs) do
    el.colspecs[i] = { spec[1], widths[i] }
  end

  return el
end
