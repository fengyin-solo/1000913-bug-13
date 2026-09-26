<template>
  <section class="page" data-module="envmonitor">
    <header class="page-head">
      <div>
        <h2>环境监控管理</h2>
        <p class="page-desc">维护环境记录，围绕记录编号、监测区域、温度值、湿度值做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="toggleCreate">{{ showCreate ? '收起登记' : '登记环境记录' }}</button>
        <button class="btn" type="button" @click="exportRows">导出环境监控清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form v-if="showCreate" class="filter-bar" @submit.prevent="submitCreate">
      <label v-for="field in createFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="createForm[field]" :placeholder="`请输入${field}`" />
      </label>
      <button class="btn primary" type="submit">提交登记</button>
    </form>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>记录编号</span>
        <input v-model="filters.keyword" placeholder="按记录编号检索" />
      </label>
      <label class="filter-item">
        <span>监测区域</span>
        <input v-model="filters.area" placeholder="按监测区域检索" />
      </label>
      <label class="filter-item">
        <span>记录状态</span>
        <select v-model="filters.status">
          <option value="">全部状态</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <form v-if="actionTarget" class="filter-bar action-panel" @submit.prevent="confirmAction">
      <span class="filter-item action-target">
        正在处理：{{ actionTarget.row['记录编号'] }}（{{ actionTarget.row['监测区域'] }}）→ {{ actionTarget.action }}
      </span>
      <label v-if="actionTarget.action === '纠正记录'" class="filter-item">
        <span>纠正说明</span>
        <input v-model="actionForm.note" placeholder="必填，说明已采取的纠正措施" />
      </label>
      <label class="filter-item">
        <span>实测湿度值</span>
        <input v-model="actionForm.humidity" placeholder="留空则沿用记录中的湿度值" />
      </label>
      <button class="btn primary" type="submit">确认执行</button>
      <button class="btn ghost" type="button" @click="actionTarget = null">取消</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <template v-if="rowActions(row).length">
              <button
                v-for="action in rowActions(row)"
                :key="action"
                class="link"
                type="button"
                @click="runAction(action, row)"
              >
                {{ action }}
              </button>
            </template>
            <span v-else class="readonly-text">已归档只读</span>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无环境监控数据，可先登记环境记录</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条环境监控记录</span>
      <span v-if="noticeMessage" class="notice-text">{{ noticeMessage }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>
type ActionResult = { ok: boolean; message?: string }

const ENDPOINT = '/api/envmonitor'
const columns = ["记录编号", "监测区域", "温度值", "湿度值", "压差值", "监测时间", "记录人员", "记录状态", "纠正说明"]
const statuses = ["在控", "偏离预警", "已归档"]
const createFields = ["记录编号", "监测区域", "温度值", "湿度值", "压差值", "监测时间", "记录人员"]
// 各状态下允许执行的动作：已归档记录只读，不能再触发预警也不能再改动
const ACTIONS_BY_STATUS: Record<string, string[]> = {
  "在控": ["偏离预警", "归档"],
  "偏离预警": ["纠正记录", "归档"],
  "已归档": [],
}

const rows = ref<Row[]>([])
const total = ref(0)
const stats = ref([
  { label: '在控记录', value: 0 },
  { label: '偏离预警', value: 0 },
  { label: '已归档', value: 0 },
])
const errorMessage = ref('')
const noticeMessage = ref('')
const filters = ref({ keyword: '', area: '', status: '' })
const showCreate = ref(false)
const createForm = ref<Record<string, string>>({})
const actionTarget = ref<{ action: string; row: Row } | null>(null)
const actionForm = ref({ note: '', humidity: '' })

function rowActions(row: Row): string[] {
  return ACTIONS_BY_STATUS[String(row.status ?? '')] ?? []
}

function toggleCreate() {
  showCreate.value = !showCreate.value
}

function resetFilters() {
  filters.value = { keyword: '', area: '', status: '' }
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

async function postJson(url: string, body: unknown): Promise<string> {
  const response = await request(url, { method: 'POST', body: JSON.stringify(body) })
  const result = (await response.json()) as ActionResult
  if (!response.ok || !result.ok) {
    throw new Error(result.message || `接口返回 ${response.status}，操作未生效`)
  }
  return result.message ?? '操作成功'
}

async function submitCreate() {
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    noticeMessage.value = await postJson(ENDPOINT, { values: { ...createForm.value } })
    createForm.value = {}
    showCreate.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '环境记录登记失败'
  }
}

function runAction(action: string, row: Row) {
  errorMessage.value = ''
  noticeMessage.value = ''
  if (action === '归档') {
    if (!window.confirm(`确认归档环境记录 ${row['记录编号']}？归档后仅可查询，不能再改动。`)) {
      return
    }
    void submitAction(row, { action })
    return
  }
  actionForm.value = { note: '', humidity: '' }
  actionTarget.value = { action, row }
}

async function confirmAction() {
  if (!actionTarget.value) {
    return
  }
  const { action, row } = actionTarget.value
  const values: Record<string, string> = { action }
  if (actionForm.value.humidity.trim()) {
    values['湿度值'] = actionForm.value.humidity.trim()
  }
  if (action === '纠正记录') {
    values['纠正说明'] = actionForm.value.note.trim()
  }
  if (await submitAction(row, values)) {
    actionTarget.value = null
  }
}

async function submitAction(row: Row, values: Record<string, string>): Promise<boolean> {
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    noticeMessage.value = await postJson(`${ENDPOINT}/${row.id}/actions`, { values })
    await reload()
    return true
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '环境监控操作失败'
    return false
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams()
  if (filters.value.keyword) query.set('keyword', filters.value.keyword)
  if (filters.value.area) query.set('area', filters.value.area)
  if (filters.value.status) query.set('status', filters.value.status)
  try {
    const [listResponse, allResponse] = await Promise.all([
      request(`${ENDPOINT}?${query}`),
      request(`${ENDPOINT}?size=200`),
    ])
    if (!listResponse.ok) {
      throw new Error('环境记录列表读取失败')
    }
    const payload = await listResponse.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    if (allResponse.ok) {
      const all = await allResponse.json()
      const items = (all.items ?? []) as Row[]
      stats.value = statuses.map((status) => ({
        label: status === '在控' ? '在控记录' : status,
        value: items.filter((item) => item.status === status).length,
      }))
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '环境监控列表读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.action-panel {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 12px;
}
.action-target {
  align-self: center;
  font-size: 13px;
}
.readonly-text {
  color: var(--muted);
  font-size: 12px;
}
.notice-text {
  color: #067647;
}
</style>
