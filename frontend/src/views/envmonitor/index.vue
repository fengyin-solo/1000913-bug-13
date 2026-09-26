<template>
  <section class="page" data-module="envmonitor">
    <header class="page-head">
      <div>
        <h2>环境监控管理</h2>
        <p class="page-desc">维护环境记录，围绕记录编号、监测区域、温度值、湿度值做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记环境记录</button>
        <button class="btn" type="button" @click="exportRows">导出环境监控清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>记录编号</span>
        <input v-model="filters.keyword" placeholder="按记录编号检索" />
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
            <button class="link" type="button" @click="openDetail(row)">详情</button>
            <template v-if="row.status === '在控'">
              <button class="link" type="button" @click="runAction('偏离预警', row)">偏离预警</button>
              <button class="link" type="button" @click="runAction('归档', row)">归档</button>
            </template>
            <button
              v-else-if="row.status === '偏离预警'"
              class="link"
              type="button"
              @click="openCorrect(row)"
            >
              纠正记录
            </button>
            <span v-else class="muted-text">已归档只读</span>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无环境监控数据，可先登记环境记录</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条环境监控记录</span>
      <span v-if="noticeMessage" class="ok-text">{{ noticeMessage }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <div v-if="createVisible" class="modal-mask" @click.self="closeDialogs">
      <div class="modal-card">
        <h3>登记环境记录</h3>
        <p class="modal-tip">同一监测区域当期只保留一条记录，重复登记会合并更新；记录编号留空时自动编号。</p>
        <label v-for="field in createFields" :key="field.key" class="form-item">
          <span>{{ field.label }}<em v-if="field.required" class="required-mark">*</em></span>
          <input v-model="createForm[field.key]" :placeholder="field.placeholder" />
        </label>
        <div class="modal-actions">
          <button class="btn primary" type="button" :disabled="submitting" @click="submitCreate">
            {{ submitting ? '提交中…' : '确认登记' }}
          </button>
          <button class="btn ghost" type="button" @click="closeDialogs">取消</button>
        </div>
      </div>
    </div>

    <div v-if="correctTarget" class="modal-mask" @click.self="closeDialogs">
      <div class="modal-card">
        <h3>纠正记录 · {{ correctTarget['记录编号'] }}</h3>
        <p class="modal-tip">纠正后记录恢复在控并保留纠正说明；读数仍在受控范围外时不能恢复。</p>
        <label v-for="field in measureFields" :key="field" class="form-item">
          <span>{{ field }}</span>
          <input v-model="correctForm[field]" />
        </label>
        <label class="form-item">
          <span>纠正说明<em class="required-mark">*</em></span>
          <textarea v-model="correctForm['纠正说明']" rows="3" placeholder="说明偏离原因与纠正措施"></textarea>
        </label>
        <div class="modal-actions">
          <button class="btn primary" type="button" :disabled="submitting" @click="submitCorrect">
            {{ submitting ? '提交中…' : '确认纠正' }}
          </button>
          <button class="btn ghost" type="button" @click="closeDialogs">取消</button>
        </div>
      </div>
    </div>

    <div v-if="detailEntry" class="modal-mask" @click.self="closeDialogs">
      <div class="modal-card">
        <h3>记录详情 · {{ detailEntry['记录编号'] }}</h3>
        <dl class="detail-list">
          <template v-for="field in detailFields" :key="field">
            <dt>{{ field }}</dt>
            <dd>{{ detailEntry[field] ?? '—' }}</dd>
          </template>
        </dl>
        <h4>纠正历史</h4>
        <ul v-if="correctionHistory.length" class="history-list">
          <li v-for="(item, index) in correctionHistory" :key="index">
            {{ item['纠正时间'] }}：{{ item['纠正说明'] }}（{{ item['纠正后状态'] }}）
          </li>
        </ul>
        <p v-else class="muted-text">暂无纠正记录</p>
        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="closeDialogs">关闭</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, any>

const ENDPOINT = '/api/envmonitor'
const columns = ["记录编号", "监测区域", "温度值", "湿度值", "压差值", "监测时间", "记录人员", "记录状态"]
const statuses = ["在控", "偏离预警", "已归档"]
const measureFields = ["温度值", "湿度值", "压差值"]
const detailFields = ["记录编号", "监测区域", "温度值", "湿度值", "压差值", "监测时间", "记录人员", "记录状态", "预警原因", "纠正说明"]
const createFields = [
  { key: '记录编号', label: '记录编号', required: false, placeholder: '留空自动编号' },
  { key: '监测区域', label: '监测区域', required: true, placeholder: '如：微生物实验室' },
  { key: '温度值', label: '温度值', required: true, placeholder: '受控范围 18~26' },
  { key: '湿度值', label: '湿度值', required: false, placeholder: '受控范围 30~70' },
  { key: '压差值', label: '压差值', required: false, placeholder: '受控范围 5~20' },
  { key: '监测时间', label: '监测时间', required: false, placeholder: '如：2026-09-26 09:00' },
  { key: '记录人员', label: '记录人员', required: false, placeholder: '' },
]

const rows = ref<Row[]>([])
const total = ref(0)
const stats = ref([
  { label: '在控记录', value: 0 },
  { label: '偏离预警', value: 0 },
  { label: '已归档', value: 0 },
  { label: '记录总数', value: 0 },
])
const errorMessage = ref('')
const noticeMessage = ref('')
const filters = ref<Record<string, string>>({ keyword: '', status: '' })
const submitting = ref(false)
const createVisible = ref(false)
const createForm = ref<Record<string, string>>({})
const correctTarget = ref<Row | null>(null)
const correctForm = ref<Record<string, string>>({})
const detailEntry = ref<Row | null>(null)

const correctionHistory = computed<Row[]>(() => {
  const history = detailEntry.value?.['纠正历史']
  return Array.isArray(history) ? history : []
})

function resetFilters() {
  filters.value = { keyword: '', status: '' }
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  createForm.value = {}
  createVisible.value = true
}

function openCorrect(row: Row) {
  correctTarget.value = row
  correctForm.value = {
    温度值: String(row['温度值'] ?? ''),
    湿度值: String(row['湿度值'] ?? ''),
    压差值: String(row['压差值'] ?? ''),
    纠正说明: '',
  }
}

function closeDialogs() {
  createVisible.value = false
  correctTarget.value = null
  detailEntry.value = null
}

async function openDetail(row: Row) {
  clearMessages()
  try {
    const response = await request(`${ENDPOINT}/${row.id}`)
    if (!response.ok) {
      throw new Error('环境记录详情读取失败')
    }
    detailEntry.value = await response.json()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '环境记录详情读取失败'
  }
}

async function postAction(id: number | string, values: Record<string, unknown>): Promise<string> {
  const response = await request(`${ENDPOINT}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  const payload = await response.json().catch(() => null)
  if (!response.ok || !payload?.ok) {
    throw new Error(payload?.message ?? payload?.detail ?? '环境监控动作未生效，请稍后重试')
  }
  return String(payload.message ?? '操作成功')
}

async function runAction(action: string, row: Row) {
  clearMessages()
  try {
    noticeMessage.value = await postAction(row.id, { action })
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '环境监控操作失败'
  }
}

async function submitCorrect() {
  const target = correctTarget.value
  if (!target || submitting.value) {
    return
  }
  if (!correctForm.value['纠正说明']?.trim()) {
    errorMessage.value = '纠正记录必须填写纠正说明'
    return
  }
  submitting.value = true
  clearMessages()
  try {
    noticeMessage.value = await postAction(target.id, { action: '纠正记录', ...correctForm.value })
    closeDialogs()
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '纠正记录提交失败'
  } finally {
    submitting.value = false
  }
}

async function submitCreate() {
  if (submitting.value) {
    return
  }
  submitting.value = true
  clearMessages()
  try {
    const response = await request(ENDPOINT, {
      method: 'POST',
      body: JSON.stringify({ values: createForm.value }),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload?.ok) {
      throw new Error(payload?.message ?? payload?.detail ?? '环境记录登记失败')
    }
    noticeMessage.value = String(payload.message ?? '环境记录已登记')
    closeDialogs()
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '环境记录登记失败'
  } finally {
    submitting.value = false
  }
}

function clearMessages() {
  errorMessage.value = ''
  noticeMessage.value = ''
}

async function refreshStats() {
  try {
    const response = await request(`${ENDPOINT}/export`)
    if (!response.ok) {
      return
    }
    const payload = await response.json()
    const items: Row[] = payload.items ?? []
    const count = (status: string) => items.filter((item) => item.status === status).length
    stats.value = [
      { label: '在控记录', value: count('在控') },
      { label: '偏离预警', value: count('偏离预警') },
      { label: '已归档', value: count('已归档') },
      { label: '记录总数', value: items.length },
    ]
  } catch {
    // 统计卡片失败不阻塞列表
  }
}

async function reload() {
  clearMessages()
  const query = new URLSearchParams()
  if (filters.value.keyword) {
    query.set('keyword', filters.value.keyword)
  }
  if (filters.value.status) {
    query.set('status', filters.value.status)
  }
  try {
    const response = await request(`${ENDPOINT}?${query.toString()}`)
    if (!response.ok) {
      throw new Error('环境记录列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    await refreshStats()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '环境监控列表读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 20;
}
.modal-card {
  background: #fff;
  border-radius: 10px;
  padding: 18px 20px;
  width: 420px;
  max-height: 80vh;
  overflow: auto;
}
.modal-card h3 { margin: 0 0 8px; font-size: 15px; }
.modal-card h4 { margin: 12px 0 6px; font-size: 13px; }
.modal-tip { color: var(--muted); font-size: 12px; margin: 0 0 10px; }
.form-item { display: block; margin-bottom: 10px; }
.form-item span { display: block; font-size: 12px; color: var(--muted); margin-bottom: 4px; }
.form-item input, .form-item textarea {
  width: 100%;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 6px 8px;
  font-size: 13px;
}
.required-mark { color: #b42318; font-style: normal; }
.modal-actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 12px; }
.detail-list { display: grid; grid-template-columns: 96px 1fr; gap: 6px 10px; margin: 0; font-size: 13px; }
.detail-list dt { color: var(--muted); }
.detail-list dd { margin: 0; }
.history-list { margin: 0; padding-left: 18px; font-size: 13px; }
.muted-text { color: var(--muted); font-size: 12px; }
.ok-text { color: #067647; }
</style>
