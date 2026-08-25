<script setup>
/**
 * 输入区组件（ChatGPT 式圆角大输入框 + 圆形发送钮）
 * 欢迎态（居中）与对话态（底部固定）两处复用；Enter 发送，自持输入值。
 */
import { ref } from 'vue'

defineProps({ sending: { type: Boolean, default: false } })
const emit = defineEmits(['send'])

const input = ref('')

function submit() {
  const q = input.value.trim()
  if (!q) return
  emit('send', q)
  input.value = ''
}
</script>

<template>
  <div class="composer">
    <el-input
      v-model="input"
      type="textarea"
      :rows="2"
      resize="none"
      placeholder="输入气象问题，Enter 发送…"
      :disabled="sending"
      @keydown.enter.exact.prevent="submit"
    />
    <button class="send-btn" :disabled="sending || !input.trim()" @click="submit">
      <span v-if="sending" class="spin">◌</span>
      <span v-else>➤</span>
    </button>
  </div>
</template>

<style scoped>
.composer {
  position: relative;
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.9);
  backdrop-filter: blur(10px);
  border: 1px solid #e2e8f0;
  box-shadow: 0 6px 24px rgba(15, 23, 42, 0.1);
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
}
.composer:focus-within {
  border-color: #7dd3fc;
  box-shadow: 0 6px 28px rgba(2, 132, 199, 0.16);
}
.composer :deep(.el-textarea__inner) {
  background: transparent;
  border: none;
  box-shadow: none !important;
  padding: 14px 56px 14px 16px;
  font-size: 14px;
  line-height: 1.7;
}
.send-btn {
  position: absolute;
  right: 10px;
  bottom: 10px;
  width: 38px; height: 38px;
  border: none;
  border-radius: 12px;
  background: linear-gradient(135deg, #0284c7, #075985);
  color: #fff;
  font-size: 16px;
  cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  transition: transform 0.15s ease, filter 0.2s ease;
}
.send-btn:hover:not(:disabled) { filter: brightness(1.1); }
.send-btn:active:not(:disabled) { transform: scale(0.94); }
.send-btn:disabled { opacity: 0.45; cursor: not-allowed; }
.spin { display: inline-block; animation: rot 0.9s linear infinite; }
@keyframes rot { to { transform: rotate(360deg); } }
</style>
