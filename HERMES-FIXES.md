# Hermes-Specific Fixes for Kiro Gateway

## 1. Web Search Interception Fix

**Problem:** Gateway intercepts ALL `web_search` tool calls and routes them through Amazon Q's MCP API, causing XML-wrapped results to bleed into chat and halt execution.

**Solution:** Make `WEB_SEARCH_ENABLED` flag control streaming interception, not just auto-injection.

**Files Changed:**
- `kiro/streaming_anthropic.py` line 355: Add `WEB_SEARCH_ENABLED` check before interception
- `kiro/streaming_openai.py` line 200: Add `WEB_SEARCH_ENABLED` check before interception
- `.env`: Set `WEB_SEARCH_ENABLED=false`

**Result:** Hermes uses native Tavily integration for web_search instead of gateway's MCP emulation.

---

## 2. Empty Response Issue (Investigation)

**Problem:** Gateway occasionally displays `"(empty result)"` in tool results when Amazon Q/Bedrock returns empty responses.

**Root Cause:** 
- Amazon Q/Bedrock occasionally returns empty tool results
- Kiro API rejects requests with empty tool result content
- Gateway inserts `"(empty result)"` placeholder to prevent API errors

**Locations:**
- `kiro/converters_core.py` line 734: `convert_tool_results_to_kiro_format()`
- `kiro/converters_core.py` line 896: `format_tool_results_for_system_prompt()`
- `kiro/converters_openai.py` line 73, 182: OpenAI format converters
- `kiro/converters_anthropic.py` line 158: Anthropic format converter

**Potential Solutions:**
1. **Skip empty results entirely** - Don't send them to the model (may break tool call chains)
2. **Use more descriptive placeholder** - e.g., `"[No output from tool]"` instead of `"(empty result)"`
3. **Log and investigate upstream** - Track when/why Amazon Q returns empty responses
4. **Make placeholder configurable** - Add env var for custom empty result text

**Recommendation:** Option 2 (better placeholder) + Option 3 (logging) for now. Option 1 requires deeper testing to ensure it doesn't break multi-step tool workflows.

---

## Next Steps

### Option A: Fork to McClean-codes org
```bash
# Fork on GitHub UI to McClean-codes/kiro-gateway
cd /home/randolph/providers/kiro-gateway
git remote add mcclean git@github.com:McClean-codes/kiro-gateway.git
git push mcclean main
```

### Option B: Submit PR to upstream
```bash
cd /home/randolph/providers/kiro-gateway
git checkout -b fix/web-search-enabled-streaming
git add kiro/streaming_anthropic.py kiro/streaming_openai.py
git commit -m "fix: respect WEB_SEARCH_ENABLED in streaming interception

The WEB_SEARCH_ENABLED flag was only controlling auto-injection
but not streaming interception. This caused the gateway to intercept
all web_search calls even when the flag was disabled.

This change makes streaming paths check WEB_SEARCH_ENABLED before
intercepting web_search tool calls, allowing clients to use their
own web_search implementations when desired."
```

### Testing
```bash
# Verify web_search uses Hermes' Tavily integration
hermes --profile sherlock
# In chat: test web_search - should NOT see XML wrappers or interception logs
```
