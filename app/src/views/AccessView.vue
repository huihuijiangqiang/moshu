<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { orgApi, type Organization, type OrganizationMember, type OrgRole } from '@/api/orgs'
import { useProjectStore } from '@/stores/project'
import { useShellStore } from '@/stores/shell'

const route = useRoute()
const shell = useShellStore()
const project = useProjectStore()
const orgs = ref<Organization[]>([])
const activeOrgId = ref('')
const members = ref<OrganizationMember[]>([])
const permissions = ref<string[]>([])
const newOrgName = ref('')
const inviteEmail = ref('')
const inviteRole = ref<OrgRole>('writer')
const message = ref('')
const projectId = computed(() => String(route.params.projectId ?? ''))
const activeOrg = computed(() => orgs.value.find((org) => org.id === activeOrgId.value) ?? null)
const canManage = computed(() => activeOrg.value?.role === 'owner')

async function loadMembers() {
  members.value = activeOrgId.value ? await orgApi.members(activeOrgId.value) : []
}

async function load() {
  try {
    ;[orgs.value, permissions.value] = await Promise.all([orgApi.list(), orgApi.projectPermissions(projectId.value)])
    activeOrgId.value = orgs.value[0]?.id ?? ''
    await loadMembers()
  } catch (error) {
    message.value = error instanceof Error ? error.message : '权限数据加载失败'
  }
}

async function createOrg() {
  if (!newOrgName.value.trim()) return
  const created = await orgApi.create(newOrgName.value.trim())
  orgs.value.push(created)
  activeOrgId.value = created.id
  newOrgName.value = ''
  message.value = '工作室已创建'
}

async function invite() {
  if (!activeOrgId.value || !inviteEmail.value.trim()) return
  const member = await orgApi.addMember(activeOrgId.value, inviteEmail.value.trim(), inviteRole.value)
  members.value.push(member)
  inviteEmail.value = ''
  message.value = `已添加 ${member.name}`
}

async function updateRole(member: OrganizationMember) {
  const updated = await orgApi.updateMember(activeOrgId.value, member.user_id, member.role)
  Object.assign(member, updated)
  message.value = `已更新 ${member.name} 的权限`
}

async function removeMember(member: OrganizationMember) {
  if (!window.confirm(`确认将 ${member.name} 移出工作室？`)) return
  await orgApi.removeMember(activeOrgId.value, member.user_id)
  members.value = members.value.filter((item) => item.user_id !== member.user_id)
}

async function attachProject() {
  if (!activeOrgId.value) return
  await orgApi.attachProject(activeOrgId.value, projectId.value)
  message.value = `《${project.project?.title ?? '当前作品'}》已加入 ${activeOrg.value?.name}`
}

watch(activeOrgId, () => void loadMembers())
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
      <form @submit.prevent="createOrg"><input v-model="newOrgName" placeholder="新工作室名称"><button class="wk-btn wk-btn-xs" type="submit">创建</button></form>
    </aside>

    <main class="access-main">
      <header>
        <div><span class="wk-label">作品权限</span><h1>{{ activeOrg?.name ?? '尚未建立工作室' }}</h1></div>
        <button v-if="activeOrg && permissions.includes('manage_project')" class="wk-btn" data-primary="true" type="button" @click="attachProject">共享当前作品</button>
      </header>
      <p v-if="message" class="access-message">{{ message }}</p>

      <template v-if="activeOrg">
        <form v-if="canManage" class="access-invite" @submit.prevent="invite">
          <input v-model="inviteEmail" type="email" placeholder="已注册用户邮箱">
          <select v-model="inviteRole"><option value="lead">主编</option><option value="writer">作者</option><option value="editor">编辑</option><option value="viewer">只读</option></select>
          <button class="wk-btn" type="submit">添加成员</button>
        </form>

        <table class="access-table">
          <thead><tr><th>成员</th><th>角色</th><th>权限范围</th><th /></tr></thead>
          <tbody><tr v-for="member in members" :key="member.user_id">
            <td><strong>{{ member.name }}</strong><small>{{ member.email }}</small></td>
            <td><select v-model="member.role" :disabled="!canManage" @change="updateRole(member)"><option value="owner">所有者</option><option value="lead">主编</option><option value="writer">作者</option><option value="editor">编辑</option><option value="viewer">只读</option></select></td>
            <td class="access-scope">{{ member.role === 'owner' ? '全部权限' : member.role === 'lead' ? '内容、设定、守卫与导出' : member.role === 'writer' ? '正文、章纲与生成' : member.role === 'editor' ? '正文、设定、告警处置与导出' : '仅查看' }}</td>
            <td><button v-if="canManage && member.role !== 'owner'" class="wk-btn wk-btn-xs" type="button" @click="removeMember(member)">移除</button></td>
          </tr></tbody>
        </table>
      </template>
      <div v-else class="access-empty">创建工作室后，可以按角色共享当前作品。</div>
    </main>
  </div>
</template>

<style scoped>
.access-page { height: 100%; display: grid; grid-template-columns: 250px minmax(0, 1fr); background: var(--panel); color: var(--ink); }
.access-orgs { border-right: var(--hair) solid var(--line); padding: 18px 0; overflow: auto; }
.access-heading { padding: 0 16px 14px; display: flex; justify-content: space-between; color: var(--ink-3); font-size: var(--fs-sm); }
.access-orgs > button { width: 100%; padding: 12px 16px; border: 0; border-left: 3px solid transparent; text-align: left; background: transparent; color: var(--ink); cursor: pointer; }
.access-orgs > button[aria-current="true"] { border-left-color: var(--accent); background: var(--paper); }
.access-orgs small, .access-table small { display: block; margin-top: 5px; color: var(--ink-3); }
.access-orgs form { margin: 18px 14px 0; display: grid; gap: 7px; }.access-orgs input { height: 34px; min-width: 0; border: var(--hair) solid var(--line); padding: 0 9px; background: var(--paper); }
.access-main { padding: 30px 34px; overflow: auto; }.access-main header { display: flex; justify-content: space-between; align-items: end; gap: 20px; margin-bottom: 24px; }.access-main h1 { margin: 6px 0 0; font-size: 24px; }.access-message { padding: 9px 12px; border-left: 3px solid var(--accent); background: var(--paper); }
.access-invite { display: grid; grid-template-columns: minmax(220px, 1fr) 140px auto; gap: 8px; max-width: 660px; margin-bottom: 22px; }.access-invite input, select { height: 36px; border: var(--hair) solid var(--line-strong); padding: 0 9px; background: var(--paper); color: var(--ink); }
.access-table { width: 100%; border-collapse: collapse; font-size: var(--fs-sm); }.access-table th { padding: 10px 8px; border-bottom: var(--hair) solid var(--line-strong); text-align: left; color: var(--ink-3); }.access-table td { padding: 12px 8px; border-bottom: var(--hair) solid var(--line); }.access-scope { color: var(--ink-2); }.access-empty { padding: 60px 0; color: var(--ink-3); }
@media (max-width: 760px) { .access-page { grid-template-columns: 1fr; overflow: auto; }.access-orgs { max-height: 230px; border-right: 0; border-bottom: var(--hair) solid var(--line); }.access-invite { grid-template-columns: 1fr; }.access-table { min-width: 680px; } }
</style>
