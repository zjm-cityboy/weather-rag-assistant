<script setup>
import { nextTick, onMounted, ref } from 'vue'
import MarkdownIt from 'markdown-it'
import { login, register } from './api/auth'
import { askStream } from './api/ask'
import ChatComposer from './components/ChatComposer.vue'
import GraphPanel from './components/GraphPanel.vue'
import WeatherCard from './components/WeatherCard.vue'

const md = new MarkdownIt({ breaks: true })       // breaks: 单换行也换行（聊天场景）

/** 渲染前清理：剥掉模型偶发输出的行内引用标注（如 [1] 或 [2][3]），来源统一由下方清单展示 */
function renderAnswer(content) {
  return md.render(content.replace(/\[\d+\](\[\d+\])*/g, ''))
}

const messages = ref([])  // [{role, content, sources, weather, graph, meta, followups, isError, streaming}]
const sending = ref(false)
const listEl = ref(null)           // 消息列表 DOM（自动滚动用）
const sessionId = ref('')

// ---------- 登录态（JWT 持久化：刷新不丢登录） ----------
const token = ref('')
const username = ref('')
const showRegister = ref(false)    // 登录/注册表单切换
const form = ref({ username: '', password: '' })
const authError = ref('')
const authBusy = ref(false)

onMounted(() => {
  token.value = localStorage.getItem('rag_token') || ''
  username.value = localStorage.getItem('rag_user') || ''
  sessionId.value = localStorage.getItem('rag_session') || ''
})

async function submitAuth() {
  authError.value = ''
  authBusy.value = true
  try {
    const fn = showRegister.value ? register : login
    const data = await fn(form.value.username.trim(), form.value.password)
    token.value = data.token
    username.value = data.username
    localStorage.setItem('rag_token', data.token)
    localStorage.setItem('rag_user', data.username)
    // 会话按用户隔离：登录返回专属 session（新登录开新会话）
    sessionId.value = data.session || `u-${data.username}-${Date.now()}`
    localStorage.setItem('rag_session', sessionId.value)
    form.value = { username: '', password: '' }
  } catch (e) {
    authError.value = e.message
  } finally {
    authBusy.value = false
  }
}

function logout() {
  token.value = ''
  username.value = ''
  sessionId.value = ''
  messages.value = []
  localStorage.removeItem('rag_token')
  localStorage.removeItem('rag_user')
  localStorage.removeItem('rag_session')
}

// ---------- 问答 ----------
const INTENT_ZH = { knowledge: '知识检索', chitchat: '闲聊', weather: '实时天气', graph: '知识图谱' }

// 四路能力各给一个示例问题（欢迎态胶囊）
const SUGGESTIONS = [
  { icon: '📚', tag: '知识检索', text: '台风是怎么形成的？' },
  { icon: '🌦️', tag: '实时天气', text: '北京今天多少度？' },
  { icon: '🕸️', tag: '灾害图谱', text: '台风会引发哪些次生灾害？' },
  { icon: '💬', tag: '闲聊', text: '你能做什么？' },
]

async function scrollBottom() {
  await nextTick()
  if (listEl.value) listEl.value.scrollTop = listEl.value.scrollHeight
}

async function send(q) {
  q = (q || '').trim()
  if (!q || sending.value) return
  sending.value = true

  messages.value.push({ role: 'user', content: q })
  messages.value.push({ role: 'assistant', content: '', sources: [],
                        weather: null, graph: [], meta: null, followups: [],
                        isError: false, streaming: true })
  const idx = messages.value.length - 1        // 当前流式回答的下标
  await scrollBottom()

  await askStream(sessionId.value, q, token.value, {
    onToken: (text) => { messages.value[idx].content += text; scrollBottom() },
    onSources: (list) => { messages.value[idx].sources = list },
    onWeather: (data) => { messages.value[idx].weather = data },
    onGraph: (triples) => { messages.value[idx].graph = triples },
    onSuggestions: (list) => { messages.value[idx].followups = list; scrollBottom() },
    onDone: (payload) => { messages.value[idx].meta = payload.meta },
    onAuthExpired: () => logout(),               // 登录过期：回到登录页
    onError: (msg) => {
      const m = messages.value[idx]
      if (!m.content) { m.content = `请求失败：${msg}`; m.isError = true }
    },
  })
  messages.value[idx].streaming = false
  sending.value = false
  await scrollBottom()
}

function sourceName(s) {
  let name = s.source.replace(/^web:/, '')          // 去掉 web: 前缀
  return name.length > 38 ? name.slice(0, 38) + '…' : name
}
</script>

<template>
  <div class="chat-wrap">
    <!-- ===== 未登录：注册/登录卡片 ===== -->
    <div v-if="!token" class="auth-view">
      <div class="auth-card">
        <div class="welcome-icon">⛅</div>
        <h2>{{ showRegister ? '创建账号' : '欢迎回来' }}</h2>
        <p class="auth-sub">气象 RAG 智能问答助手</p>
        <el-input v-model="form.username" placeholder="用户名（3~20 字符）" maxlength="20"
                  @keydown.enter="submitAuth" />
        <el-input v-model="form.password" type="password" placeholder="密码（至少 6 位）"
                  show-password maxlength="64" @keydown.enter="submitAuth" />
        <div v-if="authError" class="auth-error">{{ authError }}</div>
        <el-button type="primary" class="auth-btn" :loading="authBusy" round @click="submitAuth">
          {{ showRegister ? '注册并登录' : '登录' }}
        </el-button>
        <button class="auth-switch" @click="showRegister = !showRegister; authError = ''">
          {{ showRegister ? '已有账号？去登录' : '没有账号？注册一个' }}
        </button>
      </div>
    </div>

    <!-- ===== 已登录：主界面 ===== -->
    <template v-else>
    <!-- 头部：细条（成熟 AI 产品范式：不做渐变大卡片） -->
    <header class="chat-header">
      <span class="logo">⛅</span>
      <span class="app-name">气象 RAG 智能问答助手</span>
      <span class="divider"></span>
      <span class="tagline">知识检索 · 实时天气 · 灾害图谱 · 引用溯源</span>
      <span class="session">{{ sessionId }}</span>
      <span class="user-badge">👤 {{ username }}</span>
      <button class="logout" @click="logout">退出</button>
    </header>

    <!-- 欢迎态：标题 + 居中输入框 + 能力胶囊（ChatGPT 首屏形态，不空） -->
    <div v-if="messages.length === 0" class="welcome">
      <div class="welcome-icon">⛅</div>
      <h2>你好，我是气象知识助手</h2>
      <p>基于 2675 条气象知识块 + 灾害关系图谱的检索增强问答</p>
      <div class="welcome-composer">
        <ChatComposer :sending="sending" @send="send" />
      </div>
      <div class="chips">
        <button v-for="s in SUGGESTIONS" :key="s.text" class="chip" @click="send(s.text)">
          <span>{{ s.icon }}</span>{{ s.text }}
        </button>
      </div>
    </div>

    <!-- 对话态：文档式消息流 + 底部输入 -->
    <template v-else>
      <main class="chat-list" ref="listEl">
        <TransitionGroup name="msg">
          <div v-for="m in messages" :key="m" :class="['msg-row', m.role]">
            <!-- 用户：右侧紧凑气泡 -->
            <div v-if="m.role === 'user'" class="bubble user-bubble">{{ m.content }}</div>

            <!-- AI：全宽文档式（无气泡卡片）——头像 + 内容列 -->
            <template v-else>
              <div class="avatar">⛅</div>
              <div class="doc" :class="{ error: m.isError }">
                <div v-if="m.content" class="md" v-html="renderAnswer(m.content)"></div>
                <div v-if="m.streaming" class="cursor">▍</div>

                <WeatherCard v-if="m.weather && m.weather.now && m.weather.now.condition"
                             :weather="m.weather" />
                <GraphPanel v-if="m.graph.length" :triples="m.graph" />

                <!-- 引用来源：横向小卡片网格（Perplexity 式） -->
                <div v-if="m.sources.length" class="sources">
                  <div class="sources-title">📎 引用来源</div>
                  <div class="sources-grid">
                    <a v-for="s in m.sources" :key="s.no"
                       :href="s.url || '#'" :target="s.url ? '_blank' : undefined"
                       class="source-card" rel="noopener">
                      <span class="no">{{ s.no }}</span>
                      <span class="name">{{ sourceName(s) }}</span>
                      <span v-if="s.page" class="page">p.{{ s.page }}</span>
                    </a>
                  </div>
                </div>

                <!-- 元信息条：让"系统怎么思考的"可见（量化演示） -->
                <div v-if="m.meta" class="meta-row">
                  <span class="meta-chip chip-intent">{{ INTENT_ZH[m.meta.intent] || m.meta.intent }}</span>
                  <span class="meta-chip" v-if="m.meta.n_chunks">检索 {{ m.meta.n_chunks }} 块</span>
                  <span class="meta-chip" v-if="m.meta.top_score != null">相关度 {{ m.meta.top_score }}</span>
                  <span class="meta-chip">{{ m.meta.elapsed }}s</span>
                </div>

                <!-- 追问推荐：预测下一步问题（点击直接发送，与欢迎态胶囊同款） -->
                <div v-if="m.followups.length" class="followups">
                  <div class="sources-title">💡 你可能会问</div>
                  <div class="chips left">
                    <button v-for="q in m.followups" :key="q" class="chip" @click="send(q)">
                      <span>💬</span>{{ q }}
                    </button>
                  </div>
                </div>
              </div>
            </template>
          </div>
        </TransitionGroup>
      </main>

      <footer class="chat-footer">
        <ChatComposer :sending="sending" @send="send" />
      </footer>
    </template>
    </template>
  </div>
</template>

<style scoped>
/* ============ 设计系统：天空主题（ChatGPT/Claude 范式 + grain 纹理） ============ */
.chat-wrap {
  --brand: #0284c7;
  --brand-deep: #075985;
  --ink: #0f172a;
  --ink-2: #475569;
  --ink-3: #94a3b8;
  --line: #e2e8f0;
  --content: 768px;                 /* 对话主列宽（ChatGPT 同款尺度） */
  --z-grain: 40;

  height: 100vh;
  height: 100dvh;
  display: flex;
  flex-direction: column;
  position: relative;
  background:
    radial-gradient(600px 300px at 85% -50px, rgba(251, 191, 36, 0.10), transparent 70%),
    radial-gradient(800px 500px at -100px 30%, rgba(2, 132, 199, 0.08), transparent 70%),
    linear-gradient(180deg, #eef5fb 0%, #f7fafd 320px, #fafbfd 100%);
}

/* 噪点纹理层：打破渐变的数字平感（不挡交互） */
.chat-wrap::after {
  content: '';
  position: fixed;
  inset: 0;
  z-index: var(--z-grain);
  pointer-events: none;
  opacity: 0.035;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='120' height='120'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");
}

:focus-visible { outline: 2px solid var(--brand); outline-offset: 2px; border-radius: 4px; }

/* ---------- 登录/注册卡片 ---------- */
.auth-view {
  flex: 1;
  display: flex; align-items: center; justify-content: center;
  padding: 24px;
}
.auth-card {
  width: min(380px, 100%);
  display: flex; flex-direction: column; gap: 12px;
  padding: 34px 30px;
  background: rgba(255, 255, 255, 0.9);
  backdrop-filter: blur(10px);
  border: 1px solid var(--line);
  border-radius: 18px;
  box-shadow: 0 10px 40px rgba(15, 23, 42, 0.1);
  text-align: center;
}
.auth-card h2 { font-size: 20px; margin: 6px 0 0; color: var(--ink); font-weight: 700; }
.auth-sub { font-size: 12.5px; color: var(--ink-3); margin: 0 0 10px; }
.auth-error {
  font-size: 12.5px; color: #b91c1c;
  background: #fef2f2; border: 1px solid #fecaca;
  border-radius: 8px; padding: 7px 10px;
}
.auth-btn { margin-top: 4px; height: 40px; font-size: 14px; }
.auth-switch {
  border: none; background: none;
  font-size: 12.5px; color: var(--brand);
  cursor: pointer; padding: 4px;
}
.auth-switch:hover { text-decoration: underline; }

/* ---------- 头部：悬浮细条 ---------- */
.chat-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 20px;
  background: rgba(255, 255, 255, 0.72);
  backdrop-filter: blur(10px);
  border-bottom: 1px solid rgba(226, 232, 240, 0.8);
  font-size: 13px;
  color: var(--ink);
}
.logo { font-size: 20px; }
.app-name { font-weight: 700; letter-spacing: 0.3px; }
.divider { width: 1px; height: 14px; background: var(--line); }
.tagline { color: var(--ink-3); font-size: 12px; }
.session {
  margin-left: auto;
  font-size: 11px; color: var(--ink-3);
  background: #f1f5f9;
  padding: 2px 10px; border-radius: 10px;
  font-family: 'Bahnschrift', 'Segoe UI', sans-serif;
  font-variant-numeric: tabular-nums;
}
.user-badge {
  font-size: 12px; color: var(--brand-deep);
  background: rgba(2, 132, 199, 0.08);
  padding: 3px 12px; border-radius: 10px;
}
.logout {
  border: none; background: none;
  font-size: 12px; color: var(--ink-3);
  cursor: pointer; padding: 4px;
}
.logout:hover { color: #b91c1c; }

/* ---------- 欢迎态（首屏居中形态） ---------- */
.welcome {
  flex: 1;
  display: flex; flex-direction: column;
  align-items: center; justify-content: center;
  padding: 24px;
  gap: 6px;
}
.welcome-icon { font-size: 52px; animation: float 4s ease-in-out infinite; }
@keyframes float { 50% { transform: translateY(-4px); } }
.welcome h2 {
  font-size: 24px; margin: 8px 0 0; color: var(--ink);
  font-weight: 700; letter-spacing: -0.3px;
  text-wrap: balance;
}
.welcome p { font-size: 13.5px; color: var(--ink-2); margin: 4px 0 22px; }
.welcome-composer { width: min(640px, 100%); }
.chips {
  display: flex; flex-wrap: wrap; justify-content: center;
  gap: 10px; margin-top: 20px; max-width: 680px;
}
.chip {
  display: inline-flex; align-items: center; gap: 7px;
  padding: 8px 14px;
  background: rgba(255, 255, 255, 0.85);
  border: 1px solid var(--line);
  border-radius: 999px;
  font-size: 12.5px; color: var(--ink-2);
  cursor: pointer;
  transition: all 0.18s ease;
}
.chip:hover {
  border-color: var(--brand); color: var(--brand);
  background: #fff;
  box-shadow: 0 4px 14px rgba(2, 132, 199, 0.12);
  transform: translateY(-1px);
}
.chip:active { transform: scale(0.97); }
/* 对话态追问胶囊：左对齐（欢迎态居中） */
.chips.left { justify-content: flex-start; margin-top: 0; }

/* ---------- 对话态：文档式消息流 ---------- */
.chat-list { flex: 1; overflow-y: auto; scroll-behavior: smooth; }
.chat-list::-webkit-scrollbar { width: 6px; }
.chat-list::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 3px; }
.chat-list::-webkit-scrollbar-thumb:hover { background: #94a3b8; }

.msg-enter-active { transition: all 0.25s ease-out; }
.msg-enter-from { opacity: 0; transform: translateY(10px); }

.msg-row {
  max-width: var(--content);
  margin: 0 auto;
  padding: 10px 20px;
  display: flex; gap: 12px;
}
.msg-row.user { justify-content: flex-end; }

/* 用户消息：紧凑气泡（唯一保留的气泡） */
.bubble {
  max-width: 70%;
  padding: 10px 15px;
  border-radius: 16px;
  border-bottom-right-radius: 6px;
  font-size: 14px; line-height: 1.7;
  background: linear-gradient(135deg, #0284c7, #075985);
  color: #fff;
  box-shadow: 0 3px 10px rgba(7, 89, 133, 0.22);
  word-break: break-word;
}

/* AI 消息：全宽文档式（无卡片），头像 + 内容列铺满主列 */
.avatar {
  width: 30px; height: 30px;
  flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  font-size: 16px;
  background: linear-gradient(135deg, #e0f2fe, #f0f9ff);
  border-radius: 50%;
  border: 1px solid #bae6fd;
  margin-top: 2px;
}
.doc { flex: 1; min-width: 0; font-size: 14.5px; line-height: 1.85; color: var(--ink); }
.doc.error { color: #991b1b; }

.md :deep(p) { margin: 0 0 10px; }
.md :deep(p:last-child) { margin-bottom: 0; }
.md :deep(ul), .md :deep(ol) { padding-left: 22px; margin: 6px 0; }
.md :deep(li) { margin: 4px 0; }
.md :deep(li)::marker { color: var(--brand); }
.md :deep(strong) { color: var(--brand-deep); }
.md :deep(code) { background: #f1f5f9; padding: 1px 5px; border-radius: 4px; font-size: 13px; }
.md :deep(h1), .md :deep(h2), .md :deep(h3) { margin: 14px 0 8px; font-size: 15.5px; }

.cursor { display: inline-block; animation: blink 0.8s infinite; color: var(--brand); }
@keyframes blink { 50% { opacity: 0; } }

/* ---------- 引用来源：横向小卡片网格 ---------- */
.sources { margin-top: 14px; }
.sources-title { font-size: 12px; font-weight: 600; color: var(--ink-2); margin-bottom: 8px; }
.sources-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(230px, 1fr));
  gap: 8px;
}
.source-card {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 12px;
  background: rgba(255, 255, 255, 0.85);
  border: 1px solid var(--line);
  border-radius: 10px;
  text-decoration: none;
  font-size: 12px; color: var(--ink-2);
  transition: all 0.18s ease;
  min-width: 0;
}
.source-card:hover {
  border-color: rgba(2, 132, 199, 0.4);
  box-shadow: 0 4px 12px rgba(2, 132, 199, 0.1);
  transform: translateY(-1px);
}
.source-card .no {
  width: 18px; height: 18px;
  flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  background: rgba(2, 132, 199, 0.1);
  color: var(--brand);
  border-radius: 6px;
  font-weight: 700; font-size: 11px;
}
.source-card .name {
  flex: 1; min-width: 0;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.source-card .page {
  flex-shrink: 0;
  color: var(--ink-3); font-size: 11px;
  font-family: 'Bahnschrift', 'Segoe UI', sans-serif;
}

/* 追问推荐 */
.followups { margin-top: 14px; }

/* ---------- 元信息条 ---------- */
.meta-row { display: flex; gap: 6px; margin-top: 12px; flex-wrap: wrap; }
.meta-chip {
  font-size: 11px;
  padding: 2px 9px;
  border-radius: 9px;
  background: #f1f5f9;
  color: var(--ink-2);
  border: 1px solid var(--line);
  font-family: 'Bahnschrift', 'Segoe UI', sans-serif;
  font-variant-numeric: tabular-nums;
}
.chip-intent { background: rgba(2, 132, 199, 0.08); color: var(--brand); border-color: rgba(2, 132, 199, 0.2); }

/* ---------- 底部输入区 ---------- */
.chat-footer {
  padding: 12px 20px 18px;
  max-width: calc(var(--content) + 40px);
  width: 100%;
  margin: 0 auto;
}
</style>
