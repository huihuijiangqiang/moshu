<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { orgApi, type Organization, type OrganizationMember, type OrgRole } from '@/api/orgs'
import { useOrgStore } from '@/stores/orgs'
import { useShellStore } from '@/stores/shell'

const shell = useShellStore()
const orgStore = useOrgStore()
const organizations = ref<Organization[]>([])
const activeOrgId = ref('')
const members = ref<OrganizationMember[]>([])
const loading = ref(true)
const busy = ref(false)
const error = ref('')
const message = ref('')
const newTeamName = ref('')
const inviteEmail = ref('')
const inviteRole = ref<OrgRole>('writer')

const activeOrg = computed(() => organizations.value.find((org) => org.id === activeOrgId.value) ?? null)
const canManageMembers = computed(() => activeOrg.value?.role === 'owner')

const roleLabels: Record<OrgRole, string> = {
  owner: '团队所有者', lead: '团队管理员', writer: '作者', editor: '编辑', viewer: '只读'
}

function roleLabel(role: OrgRole) { return roleLabels[role] }

async function loadMembers() {
  if (!activeOrgId.value) { members.value = []; return }
  members.value = await orgApi.members(activeOrgId.value)
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    organizations.value = await orgApi.list()
    activeOrgId.value = organizations.value[0]?.id ?? ''
    await loadMembers()
    orgStore.organizations.splice(0, orgStore.organizations.length, ...organizations.value)
    orgStore.loaded = true
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '团队数据读取失败'
  } finally {
    loading.value = false
  }
}

async function createTeam() {
  const name = newTeamName.value.trim()
  if (!name) return
  busy.value = true
  try {
    const created = await orgApi.create(name)
    organizations.value.push(created)
    activeOrgId.value = created.id
    newTeamName.value = ''
    await loadMembers()
    orgStore.organizations.splice(0, orgStore.organizations.length, ...organizations.value)
    message.value = `已创建 ${created.name}`
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '团队创建失败'
  } finally { busy.value = false }
}

async function invite() {
  if (!activeOrgId.value || !inviteEmail.value.trim() || !canManageMembers.value) return
  busy.value = true
  try {
    const member = await orgApi.addMember(activeOrgId.value, inviteEmail.value.trim(), inviteRole.value)
    members.value.push(member)
    inviteEmail.value = ''
    message.value = `已添加 ${member.name}`
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '成员添加失败'
  } finally { busy.value = false }
}

async function updateRole(member: OrganizationMember) {
  if (!activeOrgId.value || !canManageMembers.value) return
  busy.value = true
  try {
    const updated = await orgApi.updateMember(activeOrgId.value, member.user_id, member.role)
    Object.assign(member, updated)
    message.value = `已更新 ${member.name} 的角色`
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '角色更新失败'
  } finally { busy.value = false }
}

async function removeMember(member: OrganizationMember) {
  if (!activeOrgId.value || !canManageMembers.value) return
  busy.value = true
  try {
    await orgApi.removeMember(activeOrgId.value, member.user_id)
    members.value = members.value.filter((item) => item.user_id !== member.user_id)
    message.value = `已移除 ${member.name}`
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '成员移除失败'
  } finally { busy.value = false }
}

watch(activeOrgId, () => { void loadMembers().catch(() => { error.value = '成员读取失败' }) })
onMounted(() => { shell.setCrumb('团队与成员'); void load() })
</script>

<template>
  <div class="team-page">
    <aside class="team-list">
      <div class="team-list-head"><span>团队空间</span><strong>{{ organizations.length }}</strong></div>
      <button v-for="org in organizations" :key="org.id" type="button" :aria-current="org.id === activeOrgId" @click="activeOrgId = org.id">
        <strong>{{ org.name }}</strong><small>{{ roleLabel(org.role) }} · {{ org.seats_used }}/{{ org.seats }} 席</small>
      </button>
      <form class="team-create" @submit.prevent="createTeam">
        <label><span>新建团队</span><input v-model="newTeamName" placeholder="团队名称"></label>
        <button class="wk-btn" type="submit" :disabled="busy || !newTeamName.trim()">创建团队</button>
      </form>
    </aside>

    <main class="team-main">
      <div v-if="loading" class="team-state">正在读取团队空间…</div>
      <div v-else-if="error && !activeOrg" class="team-state team-error">{{ error }}</div>
      <template v-else>
        <header class="team-header">
          <div><span class="wk-label">{{ activeOrg ? roleLabel(activeOrg.role) : '团队空间' }}</span><h1>{{ activeOrg?.name ?? '还没有团队' }}</h1><p>{{ activeOrg ? '管理成员角色与协作边界' : '创建团队后，可以邀请成员共同完成作品。' }}</p></div>
          <span v-if="activeOrg" class="team-seat">{{ activeOrg.seats_used }} / {{ activeOrg.seats }} 席位</span>
        </header>
        <p v-if="message" class="team-message" role="status">{{ message }}</p>
        <p v-if="error" class="team-error" role="alert">{{ error }}</p>
        <section v-if="activeOrg" class="team-section">
          <div class="team-section-head"><h2>成员与角色</h2><span>{{ members.length }} 人</span></div>
          <form v-if="canManageMembers" class="team-invite" @submit.prevent="invite">
            <input v-model="inviteEmail" type="email" placeholder="成员邮箱">
            <select v-model="inviteRole"><option value="lead">团队管理员</option><option value="writer">作者</option><option value="editor">编辑</option><option value="viewer">只读</option></select>
            <button class="wk-btn" data-primary="true" type="submit" :disabled="busy || !inviteEmail.trim()">添加成员</button>
          </form>
          <div class="team-table-wrap"><table class="team-table"><thead><tr><th>成员</th><th>角色</th><th>权限范围</th><th /></tr></thead><tbody>
            <tr v-for="member in members" :key="member.user_id"><td><strong>{{ member.name }}</strong><small>{{ member.email ?? member.user_id }}</small></td><td><select v-model="member.role" :disabled="!canManageMembers || member.role === 'owner'" @change="updateRole(member)"><option value="owner">团队所有者</option><option value="lead">团队管理员</option><option value="writer">作者</option><option value="editor">编辑</option><option value="viewer">只读</option></select></td><td>{{ member.role === 'owner' || member.role === 'lead' ? '成员、作品与任务管理' : member.role === 'writer' ? '正文、章纲与生成' : member.role === 'editor' ? '正文、设定与校订' : '查看作品与进度' }}</td><td><button v-if="canManageMembers && member.role !== 'owner'" class="wk-btn wk-btn-xs" type="button" :disabled="busy" @click="removeMember(member)">移除</button></td></tr>
            <tr v-if="!members.length"><td colspan="4" class="team-empty">当前团队还没有其他成员。</td></tr>
          </tbody></table></div>
        </section>
      </template>
    </main>
  </div>
</template>

<style scoped>
.team-page { height: 100%; display: grid; grid-template-columns: 250px minmax(0, 1fr); background: var(--panel); color: var(--ink); }
.team-list { overflow: auto; padding: 18px 0; border-right: var(--hair) solid var(--line); }
.team-list-head { display: flex; justify-content: space-between; padding: 0 16px 14px; color: var(--ink-3); font-size: var(--fs-sm); }
.team-list > button { width: 100%; display: block; padding: 12px 16px; border: 0; border-left: 3px solid transparent; text-align: left; background: transparent; color: inherit; cursor: pointer; }
.team-list > button[aria-current='true'] { border-left-color: var(--primary); background: var(--paper); }
.team-list strong, .team-list small { display: block; }.team-list small, .team-create span { margin-top: 5px; color: var(--ink-3); font-size: 11px; }
.team-create { display: grid; gap: 8px; margin: 20px 14px 0; padding-top: 18px; border-top: var(--hair) solid var(--line); }.team-create label { display: grid; gap: 5px; }.team-create input { height: 34px; min-width: 0; padding: 0 9px; border: var(--hair) solid var(--line-strong); background: var(--paper); color: var(--ink); }.team-create button { justify-self: start; }
.team-main { overflow: auto; padding: 32px clamp(24px, 5vw, 68px) 60px; }.team-header { display: flex; justify-content: space-between; align-items: end; gap: 20px; padding-bottom: 22px; border-bottom: var(--hair) solid var(--line-strong); }.team-header h1 { margin: 7px 0 4px; font-size: 25px; }.team-header p { margin: 0; color: var(--ink-3); }.team-seat { color: var(--ink-3); font: 11px/1 var(--font-mono); }
.team-message, .team-error { margin: 16px 0 0; padding: 9px 12px; border-left: 3px solid var(--primary); background: var(--paper); }.team-error { color: var(--alert-ink); border-left-color: var(--alert-ink); }.team-state { display: grid; min-height: 240px; place-items: center; color: var(--ink-3); }.team-section { margin-top: 28px; }.team-section-head { display: flex; justify-content: space-between; align-items: baseline; border-bottom: 2px solid var(--ink); }.team-section-head h2 { margin: 0 0 10px; font-size: 16px; }.team-section-head span { color: var(--ink-3); font-size: 11px; }.team-invite { display: grid; grid-template-columns: minmax(220px, 1fr) 150px auto; gap: 8px; margin: 18px 0; }.team-invite input, .team-invite select, .team-table select { width: 100%; min-width: 0; height: 36px; border: var(--hair) solid var(--line-strong); padding: 0 9px; background: var(--paper); color: var(--ink); }.team-table-wrap { overflow: auto; border-top: var(--hair) solid var(--line-strong); }.team-table { width: 100%; min-width: 720px; border-collapse: collapse; font-size: var(--fs-sm); }.team-table th { padding: 10px 8px; text-align: left; color: var(--ink-3); font-weight: 500; }.team-table td { padding: 12px 8px; border-bottom: var(--hair) solid var(--line); vertical-align: top; }.team-table small { display: block; margin-top: 4px; color: var(--ink-3); }.team-empty { height: 84px; text-align: center; color: var(--ink-3); }
@media (max-width: 760px) { .team-page { grid-template-columns: 1fr; overflow: auto; }.team-list { max-height: 270px; border-right: 0; border-bottom: var(--hair) solid var(--line); }.team-main { overflow: visible; padding: 24px 20px 50px; }.team-header { align-items: start; flex-direction: column; }.team-invite { grid-template-columns: 1fr; } }
</style>
