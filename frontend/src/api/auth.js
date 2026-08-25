/**
 * /auth 的客户端封装（注册/登录；token 由调用方持久化到 localStorage）
 */

/** 注册（成功即自动登录，返回 {token, username}）。 */
export async function register(username, password) {
  const res = await fetch('/api/auth/register', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || '注册失败')
  return data
}

/** 登录，返回 {token, username, session}（session 为按用户隔离的会话标识）。 */
export async function login(username, password) {
  const res = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || '登录失败')
  return data
}
