/**
 * /ask 的 SSE 客户端
 *
 * 为什么不用原生 EventSource：它只支持 GET 且不能发 JSON body，
 * 而 /ask 是 POST——用 fetch + ReadableStream 手工解析 SSE 流（行业标准做法）。
 */

/** 发起一次流式问答。
 * @param {string} sessionId 会话标识（多轮记忆的键）
 * @param {string} question 用户问题
 * @param {string} token 登录 JWT（/ask 需要登录态）
 * @param {object} handlers { onToken, onSources, onWeather, onGraph, onSuggestions, onDone, onError }
 */
export async function askStream(sessionId, question, token, handlers) {
  let res
  try {
    res = await fetch('/api/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json',
                 Authorization: `Bearer ${token}` },     // 登录态
      body: JSON.stringify({ session_id: sessionId, question }),
    })
  } catch (e) {
    handlers.onError(`无法连接后端服务：${e}`)
    return
  }
  if (res.status === 401) {                              // 登录过期：提示并交调用方处理
    handlers.onAuthExpired?.()
    return
  }

  const reader = res.body.getReader()          // 流式读取句柄
  const decoder = new TextDecoder('utf-8')
  let buf = ''                                  // 跨 chunk 的残包缓冲

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })

    // SSE 事件以空行（\n\n）分隔；最后一段可能被 TCP 切断，留在缓冲
    const events = buf.split('\n\n')
    buf = events.pop()
    for (const block of events) {
      const ev = block.match(/^event: (.+)$/m)?.[1]
      const da = block.match(/^data: (.+)$/m)?.[1]
      if (!ev || !da) continue
      const payload = JSON.parse(da)
      if (ev === 'token') handlers.onToken(payload.text)
      else if (ev === 'sources') handlers.onSources(payload)
      else if (ev === 'weather_data') handlers.onWeather(payload)
      else if (ev === 'graph_data') handlers.onGraph(payload)
      else if (ev === 'suggestions') handlers.onSuggestions?.(payload)
      else if (ev === 'done') handlers.onDone(payload)
      else if (ev === 'error') handlers.onError(payload.message)
    }
  }
}
