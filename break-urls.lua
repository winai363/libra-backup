-- Pandoc Lua filter: break all URLs to prevent overflow in PDF

-- Handle Link elements (autolinks and markdown links)
function Link(el)
  local url = el.target
  local text = pandoc.utils.stringify(el.content)
  if text == url or text == "" then
    return pandoc.RawInline('latex', '\\url{' .. url .. '}')
  end
  return el
end

-- Handle plain text that contains URLs (not wrapped in links)
function Str(el)
  if el.text:match("https?://") then
    return pandoc.RawInline('latex', '\\url{' .. el.text .. '}')
  end
  return el
end
