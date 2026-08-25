<script setup>
/**
 * 天气实况卡片：和风新版嵌套结构 → 结构化展示（纯展示组件，props 单向流入）
 * 渐变主题按天气现象切换（晴/云/雨/雷暴/雪/雾），mock 结构无 condition 字段由父级拦住不渲染
 */
import { computed } from 'vue'

const props = defineProps({
  weather: { type: Object, required: true },   // {city, now: {condition, temperature, ...}}
})

// 16 方位罗盘缩写 → 中文（和风 wind.direction.compass）
const WIND_ZH = { n: '北', nne: '北北东', ne: '东北', ene: '东北东', e: '东', ese: '东南东',
                  se: '东南', sse: '南南东', s: '南', ssw: '南南西', sw: '西南', wsw: '西南西',
                  w: '西', wnw: '西北西', nw: '西北', nnw: '北北西' }
const WICON = { 晴: '☀️', 多云: '⛅', 阴: '☁️', 小雨: '🌦️', 中雨: '🌧️', 大雨: '🌧️',
                暴雨: '⛈️', 雷阵雨: '⛈️', 雾: '🌫️', 小雪: '🌨️', 雪: '❄️' }

const text = computed(() => props.weather?.now?.condition?.text || '')
const icon = computed(() => WICON[text.value] || '🌤️')
// humidity 为 0-1 小数（新版接口），换算百分比
const humidityPct = computed(() => {
  const h = props.weather?.now?.humidity
  return h == null ? null : Math.round(h * 100)
})
const windDir = computed(() => WIND_ZH[props.weather?.now?.wind?.direction?.compass] || '')
// 主题 class：按现象切换渐变
const theme = computed(() => {
  const t = text.value
  if (t.includes('雷') || t.includes('暴雨')) return 'theme-storm'
  if (t.includes('雨')) return 'theme-rain'
  if (t.includes('雪')) return 'theme-snow'
  if (t.includes('雾') || t.includes('霾')) return 'theme-fog'
  if (t.includes('云') || t.includes('阴')) return 'theme-cloudy'
  return 'theme-sunny'
})
const visibilityKm = computed(() =>
  Math.round((props.weather?.now?.visibility?.value || 0) / 1000))
</script>

<template>
  <div :class="['weather-card', theme]">
    <div class="w-head">
      <span class="w-icon">{{ icon }}</span>
      <div class="w-city">
        <b>{{ weather.city }}</b>
        <span>{{ text }} · 实况</span>
      </div>
    </div>
    <div class="w-temp">
      <span class="w-num">{{ weather.now.temperature.value }}<i>°C</i></span>
      <span class="w-feels">体感 {{ weather.now.feelsLike.value }}°C</span>
    </div>
    <div class="w-grid">
      <div class="w-cell"><span>💧 湿度</span><b>{{ humidityPct }}%</b></div>
      <div class="w-cell"><span>🌬 {{ windDir }}风</span><b>{{ weather.now.wind.scale }} 级</b></div>
      <div class="w-cell"><span>☔ 降水</span><b>{{ weather.now.precipitation.amount.value }} mm</b></div>
      <div class="w-cell"><span>👁 能见度</span><b>{{ visibilityKm }} km</b></div>
    </div>
  </div>
</template>

<style scoped>
.weather-card {
  margin-top: 12px;
  padding: 16px 18px;
  border-radius: 14px;
  border: 1px solid rgba(255, 255, 255, 0.6);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.4), 0 4px 14px rgba(15, 23, 42, 0.08);
}
.theme-sunny  { background: linear-gradient(135deg, #fef9c3 0%, #bae6fd 100%); color: #713f12; }
.theme-cloudy { background: linear-gradient(135deg, #e2e8f0 0%, #dbeafe 100%); color: #1e3a5f; }
.theme-rain   { background: linear-gradient(135deg, #c7d2fe 0%, #a5c9e8 100%); color: #1e3a5f; }
.theme-storm  { background: linear-gradient(135deg, #64748b 0%, #475569 100%); color: #f1f5f9; }
.theme-snow   { background: linear-gradient(135deg, #f0f9ff 0%, #e0e7ff 100%); color: #3730a3; }
.theme-fog    { background: linear-gradient(135deg, #f1f5f9 0%, #e2e8f0 100%); color: #475569; }

.w-head { display: flex; align-items: center; gap: 12px; }
.w-icon { font-size: 38px; filter: drop-shadow(0 2px 4px rgba(0, 0, 0, 0.15)); }
.w-city { display: flex; flex-direction: column; }
.w-city b { font-size: 16px; letter-spacing: 0.5px; }
.w-city span { font-size: 12px; opacity: 0.75; }
.w-temp { display: flex; align-items: baseline; gap: 10px; margin: 10px 0; }
/* 数字用 DIN 风系统字体（Windows 自带 Bahnschrift），气象仪表感 */
.w-num {
  font-family: 'Bahnschrift', 'Segoe UI', sans-serif;
  font-size: 40px; font-weight: 700; letter-spacing: -1px;
  font-variant-numeric: tabular-nums;
}
.w-num i { font-size: 18px; font-style: normal; font-weight: 500; opacity: 0.7; }
.w-feels { font-size: 12px; opacity: 0.75; }
.w-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.w-cell {
  display: flex; justify-content: space-between; align-items: center;
  padding: 7px 12px;
  background: rgba(255, 255, 255, 0.55);
  border: 1px solid rgba(255, 255, 255, 0.7);
  border-radius: 10px;
  font-size: 12px;
}
.w-cell span { opacity: 0.72; }
.w-cell b {
  font-family: 'Bahnschrift', 'Segoe UI', sans-serif;
  font-variant-numeric: tabular-nums; font-weight: 600;
}
</style>
