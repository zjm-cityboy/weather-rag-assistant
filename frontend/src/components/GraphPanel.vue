<script setup>
/**
 * 灾害关系图谱面板：三元组 → ECharts 力导向图（纯展示组件）
 * 节点按 5 类型着色、大小按连接度、边带关系标签；深色"嵌入式分析面板"风格。
 * 挂载时机由父级 v-if 保证（triples 非空才渲染），onMounted 一次成图。
 */
import * as echarts from 'echarts'
import { onMounted, ref } from 'vue'

const props = defineProps({
  triples: { type: Array, required: true },   // [{head, head_type, relation, tail, tail_type}]
})

// 节点类型 → 颜色/分类（与后端 Cypher 的 label 一一对应；深底高饱和）
const NODE_CATEGORIES = [
  { name: '灾害', color: '#f87171' },
  { name: '现象', color: '#60a5fa' },
  { name: '防御措施', color: '#4ade80' },
  { name: '成因条件', color: '#fbbf24' },
  { name: '预警信号', color: '#c084fc' },
]
const TYPE_ZH = { Disaster: '灾害', Phenomenon: '现象', Measure: '防御措施',
                  Condition: '成因条件', Signal: '预警信号' }

const el = ref(null)          // 画布容器（模板 ref，不查 DOM id）

onMounted(() => {
  // 三元组 → 去重节点集合 + 边（节点大小按连接度：越枢纽越大）
  const nodeMap = new Map()
  const degree = new Map()
  const links = props.triples.map(t => {
    nodeMap.set(t.head, TYPE_ZH[t.head_type] || '现象')
    nodeMap.set(t.tail, TYPE_ZH[t.tail_type] || '现象')
    degree.set(t.head, (degree.get(t.head) || 0) + 1)
    degree.set(t.tail, (degree.get(t.tail) || 0) + 1)
    return { source: t.head, target: t.tail, value: t.relation, lineStyle: { width: 1.5 } }
  })
  const nodes = [...nodeMap.entries()].map(([name, cat]) => ({
    name, category: NODE_CATEGORIES.findIndex(c => c.name === cat),
    symbolSize: 16 + Math.min(degree.get(name) || 0, 8) * 3,
  }))

  echarts.init(el.value).setOption({
    tooltip: { formatter: p => p.dataType === 'edge'
      ? `${p.data.source} →[${p.data.value}]→ ${p.data.target}` : p.name },
    legend: { data: NODE_CATEGORIES.map(c => c.name), bottom: 0, itemWidth: 12,
              textStyle: { fontSize: 10, color: '#94a3b8' } },
    series: [{
      type: 'graph', layout: 'force', roam: true,
      force: { repulsion: 140, edgeLength: 55, gravity: 0.08 },
      categories: NODE_CATEGORIES,
      data: nodes, links,
      label: { show: true, fontSize: 10, color: '#e2e8f0' },
      edgeLabel: { show: true, fontSize: 9, color: '#64748b', formatter: '{c}' },
      lineStyle: { color: '#334155', curveness: 0.1 },
      emphasis: { focus: 'adjacency' },      // 悬停聚焦邻接节点
    }],
  })
})
</script>

<template>
  <div class="graph-block">
    <div class="graph-titlebar">
      <span class="graph-title">🕸️ 灾害关系图谱</span>
      <span class="graph-hint">检索到的子图 · 可拖拽缩放</span>
    </div>
    <div ref="el" class="graph-canvas"></div>
  </div>
</template>

<style scoped>
.graph-block { margin-top: 12px; }
/* 嵌入式分析面板：标题栏 + 深蓝画布一体成型（不再是浅页面里的突兀黑块） */
.graph-titlebar {
  display: flex; align-items: baseline; gap: 8px;
  padding: 8px 12px;
  background: #16283e;
  border: 1px solid #1e3a5f;
  border-bottom: none;
  border-radius: 12px 12px 0 0;
}
.graph-title { font-size: 12px; font-weight: 600; color: #cbd5e1; }
.graph-hint { font-size: 11px; color: #64748b; }
.graph-canvas {
  width: 100%; height: 320px;
  background:
    radial-gradient(400px 200px at 70% 0%, rgba(56, 89, 138, 0.35), transparent 70%),
    #0c1a2b;
  border: 1px solid #1e3a5f;
  border-top: 1px solid #16283e;
  border-radius: 0 0 12px 12px;
}
</style>
