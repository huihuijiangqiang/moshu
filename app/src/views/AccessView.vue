<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ApiError } from '@/api/http'
import {
  orgApi,
  type AssignmentStatus,
  type ChapterAssignment,
  type Organization,
  type OrganizationMember,
  type OrgRole,
  type ProductionBoard
} from '@/api/orgs'
import { useProjectStore } from '@/stores/project'
import { useShellStore } from '@/stores/shell'

const route = useRoute()
const shell = useShellStore()
const project = useProjectStore()
const orgs = ref<Organization[]>([])
const activeOrgId = ref('')
const members = ref<OrganizationMember[]>([])
const production = ref<ProductionBoard | null>(null)
const permissions = ref<string[]>([])
const view = ref<'production' | 'members'>('production')
const newOrgName = ref('')
const inviteEmail = ref('')
const inviteRole = ref<OrgRole>('writer')
const assignmentChapterId = ref('')
const assignmentMemberId = ref('')
const assignmentNotes = ref('')
const busy = ref(false)
const message = ref('')
const error = ref('')

const projectId = computed(() => String(route.params.projectId ?? ''))
const activeOrg = computed(() => orgs.value.find((org) => org.id === activeOrgId.value) ?? null)
const projectAttached = computed(() => !!activeOrgId.value && project.project?.orgId === activeOrgId.value)
const canManageMembers = computed(() => activeOrg.value?.role === 'owner')
const canManageProduction = computed(() => production.value?.can_manage ?? ['owner', 'lead'].includes(activeOrg.value?.role ?? ''))
const assignableMembers = computed(() => members.value.filter((member) => member.role !== 'viewer'))
const activeAssignments = computed(() => production.value?.assignments.filter((item) => ['assigned', 'claimed'].includes(item.status)) ?? [])
const completedAssignments = computed(() => production.value?.assignments.filter((item) => item.status === 'completed') ?? [])
const totalCompletedWords = computed(() => production.value?.members.reduce((sum, member) => sum + member.completed_words, 0) ?? 0)
const statusCounts = computed(() => ({
  assigned: production.value?.assignments.filter((item) => item.status === 'assigned').length ?? 0,
  claimed: production.value?.assignments.filter((item) => item.status === 'claimed').length ?? 0,
  completed: completedAssignments.value.length,
  returned: production.value?.assignments.filter((item) => item.status === 'returned').length ?? 0
}))

const statusLabel: Record<AssignmentStatus, string> = {
  assigned: '待领取', claimed: '写作中', completed: '已完成', returned: '已退回'
}

function setFeedback(nextMessage = '', nextError = '') {
  message.value = nextMessage
  error.value = nextError
}

function errorMessage(reason: unknown, fallback: string) {
  if (reason instanceof ApiError && reason.status === 409) return '当前作品尚未加入这个工作室。'
  return reason instanceof Error && reason.message ? reason.message : fallback
}

async function loadWorkspace() {
  members.value = []
  production.value = null
  if (!activeOrgId.value) return
  try {
    members.value = await orgApi.members(activeOrgId.value)
    if (projectAttached.value) production.value = await orgApi.production(activeOrgId.value, projectId.value)
  } catch (reason) {
    setFeedback('', errorMessage(reason, '协作数据加载失败'))
  }
}

async function load() {
  try {
    await project.load(projectId.value)
    ;[orgs.value, permissions.value] = await Promise.all([orgApi.list(), orgApi.projectPermissions(projectId.value)])
    const attached = project.project?.orgId
    activeOrgId.value = attached && orgs.value.some((org) => org.id === attached) ? attached : (orgs.value[0]?.id ?? '')
    await loadWorkspace()
  } catch (reason) {
    setFeedback('', errorMessage(reason, '权限数据加载失败'))
  }
}

async function run(action: () => Promise<void>, success: string) {
  if (busy.value) return
  busy.value = true
  setFeedback()
  try {
    await action()
    setFeedback(success)
  } catch (reason) {
    setFeedback('', errorMessage(reason, '操作没有完成'))
  } finally {
    busy.value = false
  }
}

async function createOrg() {
  const name = newOrgName.value.trim()
  if (!name) return
  await run(async () => {
    const created = await orgApi.create(name)
    orgs.value.push(created)
    activeOrgId.value = created.id
    newOrgName.value = ''
    await loadWorkspace()
  }, '工作室已创建')
}

async function invite() {
  const email = inviteEmail.value.trim()
  if (!activeOrgId.value || !email) return
  await run(async () => {
    const member = await orgApi.addMember(activeOrgId.value, email, inviteRole.value)
    members.value.push(member)
    inviteEmail.value = ''
    if (projectAttached.value) production.value = await orgApi.production(activeOrgId.value, projectId.value)
  }, '成员已加入工作室')
}

async function updateRole(member: OrganizationMember) {
  await run(async () => {
    const updated = await orgApi.updateMember(activeOrgId.value, member.user_id, member.role)
    Object.assign(member, updated)
    if (projectAttached.value) production.value = await orgApi.production(activeOrgId.value, projectId.value)
  }, `${member.name} 的权限已更新`)
}

async function removeMember(member: OrganizationMember) {
  if (!window.confirm(`确认将 ${member.name} 移出工作室？`)) return
  await run(async () => {
    await orgApi.removeMember(activeOrgId.value, member.user_id)
    members.value = members.value.filter((item) => item.user_id !== member.user_id)
    if (projectAttached.value) production.value = await orgApi.production(activeOrgId.value, projectId.value)
  }, `${member.name} 已移出工作室`)
}

async function attachProject() {
  if (!activeOrgId.value) return
  await run(async () => {
    await orgApi.attachProject(activeOrgId.value, projectId.value)
    if (project.project) project.project.orgId = activeOrgId.value
    production.value = await orgApi.production(activeOrgId.value, projectId.value)
  }, `《${project.project?.title ?? '当前作品'}》已加入 ${activeOrg.value?.name}`)
}

async function assignChapter() {
  if (!assignmentChapterId.value || !assignmentMemberId.value) return
  await run(async () => {
    await orgApi.assignChapter(
      activeOrgId.value,
      projectId.value,
      assignmentChapterId.value,
      assignmentMemberId.value,
      assignmentNotes.value.trim()
    )
    production.value = await orgApi.production(activeOrgId.value, projectId.value)
    assignmentNotes.value = ''
  }, '章节任务已派发')
}

async function updateAssignment(item: ChapterAssignment, status: Exclude<AssignmentStatus, 'assigned'>) {
  await run(async () => {
    await orgApi.updateAssignment(activeOrgId.value, projectId.value, item.id, status)
    production.value = await orgApi.production(activeOrgId.value, projectId.value)
  }, status === 'claimed' ? '任务已领取' : status === 'completed' ? '任务已完成' : '任务已退回')
}

async function removeAssignment(item: ChapterAssignment) {
  if (!window.confirm(`确认取消《${item.chapter_title}》的任务分派？`)) return
  await run(async () => {
    await orgApi.removeAssignment(activeOrgId.value, projectId.value, item.id)
    production.value = await orgApi.production(activeOrgId.value, projectId.value)
  }, '章节任务已取消')
}

function isMine(item: ChapterAssignment) {
  return item.assigned_to === production.value?.current_user_id
}

watch(activeOrgId, () => void loadWorkspace())
onMounted(() => {
  shell.setCrumb('协作与权限')
  void load()
})
</script>

<template>
  <div class="access-page">
    <aside class="access-orgs">
      <div class="access-heading"><strong>工作室</strong><span>{{ orgs.length }}</span></div>
      <button v-for="org in orgs" :key="org.id" type="button" :aria-current="org.id === activeOrgId" @click="activeOrgId = org.id">
        <strong>{{ org.name }}</strong><small>{{ org.role }} · {{ org.seats_used }}/{{ org.seats }} 席</small>
      </button>
      <form @submit.prevent="createOrg"><input v-model="newOrgName" placeholder="新工作室名称"><button class="wk-btn wk-btn-xs" type="submit" :disabled="busy">创建</button></form>
    </aside>

    <main class="access-main">
      <header class="access-header">
        <div><span class="wk-label">工作室产线</span><h1>{{ activeOrg?.name ?? '尚未建立工作室' }}</h1><p>{{ project.project?.title ?? '当前作品' }}</p></div>
        <button v-if="activeOrg && !projectAttached && permissions.includes('manage_project')" class="wk-btn" data-primary="true" type="button" :disabled="busy" @click="attachProject">共享当前作品</button>
      </header>

      <div v-if="activeOrg" class="access-tabs" role="tablist" aria-label="协作视图">
        <button type="button" role="tab" :aria-selected="view === 'production'" @click="view = 'production'">章节任务</button>
        <button type="button" role="tab" :aria-selected="view === 'members'" @click="view = 'members'">成员权限</button>
      </div>
      <p v-if="message" class="access-message" role="status">{{ message }}</p>
      <p v-if="error" class="access-error" role="alert">{{ error }}</p>

      <template v-if="activeOrg && view === 'production'">
        <div v-if="!projectAttached" class="access-empty">
          <strong>作品尚未进入这个工作室</strong>
          <p>由作品所有者共享后，主编才能派章，作者才能领取任务。</p>
        </div>
        <template v-else-if="production">
          <section class="production-strip" aria-label="任务状态概览">
            <div><span>待领取</span><strong>{{ statusCounts.assigned }}</strong></div>
            <div><span>写作中</span><strong>{{ statusCounts.claimed }}</strong></div>
            <div><span>已完成</span><strong>{{ statusCounts.completed }}</strong></div>
            <div><span>完成字数</span><strong>{{ totalCompletedWords.toLocaleString() }}</strong></div>
          </section>

          <form v-if="canManageProduction" class="assignment-form" @submit.prevent="assignChapter">
            <label><span>章节</span><select v-model="assignmentChapterId" required><option value="" disabled>选择章节</option><option v-for="chapter in project.chapters" :key="chapter.id" :value="chapter.id">第 {{ chapter.index }} 章 · {{ chapter.title }}</option></select></label>
            <label><span>负责人</span><select v-model="assignmentMemberId" required><option value="" disabled>选择成员</option><option v-for="member in assignableMembers" :key="member.user_id" :value="member.user_id">{{ member.name }} · {{ member.role }}</option></select></label>
            <label><span>任务说明</span><input v-model="assignmentNotes" maxlength="500" placeholder="交稿范围、重点或截止约定"></label>
            <button class="wk-btn" data-primary="true" type="submit" :disabled="busy || !assignmentChapterId || !assignmentMemberId">派发章节</button>
          </form>

          <section class="production-section" aria-labelledby="task-list-title">
            <div class="section-title"><div><span class="wk-label">CHAPTER LEDGER</span><h2 id="task-list-title">章节任务</h2></div><small>{{ activeAssignments.length }} 项进行中</small></div>
            <div class="task-table-wrap">
              <table class="task-table">
                <thead><tr><th>章节</th><th>负责人</th><th>状态</th><th>当前字数</th><th>任务说明</th><th>操作</th></tr></thead>
                <tbody>
                  <tr v-for="item in production.assignments" :key="item.id" :data-status="item.status">
                    <td><strong>第 {{ item.chapter_index }} 章</strong><small>{{ item.chapter_title }}</small></td>
                    <td><strong>{{ item.assignee_name }}</strong><small>由 {{ item.assigner_name }} 派发</small></td>
                    <td><span class="task-status">{{ statusLabel[item.status] }}</span></td>
                    <td>{{ item.words.toLocaleString() }}</td>
                    <td class="task-notes">{{ item.notes || '未填写' }}</td>
                    <td><div class="task-actions">
                      <button v-if="item.status === 'assigned' && isMine(item)" class="wk-btn wk-btn-xs" type="button" :disabled="busy" @click="updateAssignment(item, 'claimed')">领取</button>
                      <button v-if="item.status === 'claimed' && (isMine(item) || canManageProduction)" class="wk-btn wk-btn-xs" data-primary="true" type="button" :disabled="busy" @click="updateAssignment(item, 'completed')">完成</button>
                      <button v-if="['assigned', 'claimed'].includes(item.status) && (isMine(item) || canManageProduction)" class="wk-btn wk-btn-xs" type="button" :disabled="busy" @click="updateAssignment(item, 'returned')">退回</button>
                      <button v-if="canManageProduction" class="wk-btn wk-btn-xs" type="button" :disabled="busy" @click="removeAssignment(item)">取消</button>
                    </div></td>
                  </tr>
                  <tr v-if="!production.assignments.length"><td colspan="6" class="table-empty">还没有章节任务。主编派出第一章后，任务会出现在这里。</td></tr>
                </tbody>
              </table>
            </div>
          </section>

          <section class="production-section" aria-labelledby="member-output-title">
            <div class="section-title"><div><span class="wk-label">OUTPUT BOARD</span><h2 id="member-output-title">成员产量</h2></div><small>按当前章节字数统计</small></div>
            <div class="output-grid">
              <article v-for="member in production.members" :key="member.user_id">
                <header><strong>{{ member.name }}</strong><span>{{ member.role }}</span></header>
                <div><span>进行中</span><b>{{ member.assigned_count + member.claimed_count }} 章</b></div>
                <div><span>已完成</span><b>{{ member.completed_count }} 章</b></div>
                <div><span>完成字数</span><b>{{ member.completed_words.toLocaleString() }}</b></div>
              </article>
            </div>
          </section>
        </template>
      </template>

      <template v-if="activeOrg && view === 'members'">
        <form v-if="canManageMembers" class="access-invite" @submit.prevent="invite">
          <input v-model="inviteEmail" type="email" placeholder="已注册用户邮箱">
          <select v-model="inviteRole"><option value="lead">主编</option><option value="writer">作者</option><option value="editor">编辑</option><option value="viewer">只读</option></select>
          <button class="wk-btn" type="submit" :disabled="busy">添加成员</button>
        </form>

        <div class="task-table-wrap">
          <table class="access-table">
            <thead><tr><th>成员</th><th>角色</th><th>权限范围</th><th /></tr></thead>
            <tbody><tr v-for="member in members" :key="member.user_id">
              <td><strong>{{ member.name }}</strong><small>{{ member.email }}</small></td>
              <td><select v-model="member.role" :disabled="!canManageMembers || busy" @change="updateRole(member)"><option value="owner">所有者</option><option value="lead">主编</option><option value="writer">作者</option><option value="editor">编辑</option><option value="viewer">只读</option></select></td>
              <td class="access-scope">{{ member.role === 'owner' ? '全部权限' : member.role === 'lead' ? '内容、设定、守卫、审稿与派章' : member.role === 'writer' ? '正文、章纲、生成与领取任务' : member.role === 'editor' ? '正文、设定、告警处置、审稿与导出' : '仅查看' }}</td>
              <td><button v-if="canManageMembers && member.role !== 'owner'" class="wk-btn wk-btn-xs" type="button" :disabled="busy" @click="removeMember(member)">移除</button></td>
            </tr></tbody>
          </table>
        </div>
      </template>
      <div v-if="!activeOrg" class="access-empty">创建工作室后，可以共享作品、派发章节并查看成员产量。</div>
    </main>
  </div>
</template>

<style scoped>
.access-page { height: 100%; display: grid; grid-template-columns: 250px minmax(0, 1fr); background: var(--panel); color: var(--ink); }
.access-orgs { border-right: var(--hair) solid var(--line); padding: 18px 0; overflow: auto; }
.access-heading { padding: 0 16px 14px; display: flex; justify-content: space-between; color: var(--ink-3); font-size: var(--fs-sm); }
.access-orgs > button { width: 100%; padding: 12px 16px; border: 0; border-left: 3px solid transparent; text-align: left; background: transparent; color: var(--ink); cursor: pointer; }
.access-orgs > button[aria-current="true"] { border-left-color: var(--accent); background: var(--paper); }
.access-orgs small, .access-table small, .task-table small { display: block; margin-top: 5px; color: var(--ink-3); }
.access-orgs form { margin: 18px 14px 0; display: grid; gap: 7px; }
.access-orgs input { height: 34px; min-width: 0; border: var(--hair) solid var(--line); padding: 0 9px; background: var(--paper); }
.access-main { padding: 30px 34px 60px; overflow: auto; }
.access-header { display: flex; justify-content: space-between; align-items: end; gap: 20px; margin-bottom: 18px; }
.access-header h1 { margin: 6px 0 4px; font-size: 24px; letter-spacing: 0; }
.access-header p { margin: 0; color: var(--ink-3); }
.access-tabs { display: flex; gap: 18px; border-bottom: var(--hair) solid var(--line-strong); }
.access-tabs button { min-height: 40px; padding: 0 2px; border: 0; border-bottom: 2px solid transparent; background: transparent; color: var(--ink-3); font: 700 var(--fs-sm)/1 var(--font-body); cursor: pointer; }
.access-tabs button[aria-selected="true"] { border-bottom-color: var(--ink); color: var(--ink); }
.access-message, .access-error { margin: 16px 0 0; padding: 9px 12px; border-left: 3px solid var(--accent); background: var(--paper); }
.access-error { border-left-color: var(--alert-ink); color: var(--alert-ink); }
.production-strip { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); margin-top: 24px; border-top: var(--hair) solid var(--line-strong); border-bottom: var(--hair) solid var(--line-strong); }
.production-strip > div { min-height: 94px; display: grid; align-content: center; gap: 7px; padding: 14px 18px; border-right: var(--hair) solid var(--line); }
.production-strip > div:last-child { border-right: 0; }
.production-strip span { color: var(--ink-3); font-size: var(--fs-sm); }
.production-strip strong { font: 700 23px/1 var(--font-mono); }
.assignment-form { display: grid; grid-template-columns: minmax(180px, .8fr) minmax(160px, .65fr) minmax(220px, 1fr) auto; align-items: end; gap: 9px; padding: 22px 0; border-bottom: var(--hair) solid var(--line); }
.assignment-form label { min-width: 0; display: grid; gap: 6px; }
.assignment-form label > span { color: var(--ink-3); font-size: var(--fs-xs); }
.assignment-form input, .assignment-form select, .access-invite input, .access-invite select, .access-table select { width: 100%; min-width: 0; height: 36px; border: var(--hair) solid var(--line-strong); padding: 0 9px; background: var(--paper); color: var(--ink); }
.production-section { margin-top: 34px; }
.section-title { display: flex; justify-content: space-between; align-items: end; margin-bottom: 12px; }
.section-title h2 { margin: 5px 0 0; font-size: 18px; letter-spacing: 0; }
.section-title > small { color: var(--ink-3); }
.task-table-wrap { overflow-x: auto; }
.task-table, .access-table { width: 100%; min-width: 850px; border-collapse: collapse; font-size: var(--fs-sm); }
.task-table th, .access-table th { padding: 10px 8px; border-bottom: var(--hair) solid var(--line-strong); text-align: left; color: var(--ink-3); font-weight: 500; }
.task-table td, .access-table td { padding: 12px 8px; border-bottom: var(--hair) solid var(--line); vertical-align: top; }
.task-table tr[data-status="completed"] { color: var(--ink-3); }
.task-status { display: inline-block; padding-left: 10px; border-left: 3px solid var(--line-strong); font-weight: 700; }
tr[data-status="assigned"] .task-status { border-left-color: var(--accent); }
tr[data-status="claimed"] .task-status { border-left-color: var(--primary); }
.task-notes { max-width: 260px; color: var(--ink-2); line-height: 1.55; }
.task-actions { display: flex; flex-wrap: wrap; gap: 5px; }
.table-empty { height: 100px; color: var(--ink-3); text-align: center; vertical-align: middle !important; }
.output-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); border-top: var(--hair) solid var(--line-strong); }
.output-grid article { min-width: 0; padding: 16px 18px; border-right: var(--hair) solid var(--line); border-bottom: var(--hair) solid var(--line); }
.output-grid article header { display: flex; justify-content: space-between; gap: 12px; margin-bottom: 15px; }
.output-grid article header span { color: var(--ink-3); font-size: var(--fs-xs); text-transform: uppercase; }
.output-grid article > div { display: flex; justify-content: space-between; gap: 12px; margin-top: 7px; color: var(--ink-3); font-size: var(--fs-sm); }
.output-grid article b { color: var(--ink); font-family: var(--font-mono); }
.access-invite { display: grid; grid-template-columns: minmax(220px, 1fr) 140px auto; gap: 8px; max-width: 660px; margin: 24px 0 22px; }
.access-scope { color: var(--ink-2); }
.access-empty { padding: 54px 0; border-bottom: var(--hair) solid var(--line); color: var(--ink-3); }
.access-empty strong { display: block; margin-bottom: 8px; color: var(--ink); }
.access-empty p { margin: 0; }
@media (max-width: 980px) { .assignment-form { grid-template-columns: 1fr 1fr; }.assignment-form label:nth-child(3) { grid-column: 1 / -1; } }
@media (max-width: 760px) { .access-page { grid-template-columns: 1fr; overflow: auto; }.access-orgs { max-height: 230px; border-right: 0; border-bottom: var(--hair) solid var(--line); }.access-main { overflow: visible; padding: 24px 20px 50px; }.access-header { align-items: start; flex-direction: column; }.production-strip { grid-template-columns: repeat(2, 1fr); }.production-strip > div:nth-child(2) { border-right: 0; }.production-strip > div:nth-child(-n + 2) { border-bottom: var(--hair) solid var(--line); }.assignment-form, .access-invite { grid-template-columns: 1fr; }.assignment-form label:nth-child(3) { grid-column: auto; } }
</style>
