import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { USE_MOCK } from '@/api/http'
import { orgApi, type OrgRole, type Organization } from '@/api/orgs'

const ROLE_WEIGHT: Record<OrgRole, number> = {
  owner: 5,
  lead: 4,
  editor: 3,
  writer: 2,
  viewer: 1
}

export const useOrgStore = defineStore('orgs', () => {
  const organizations = ref<Organization[]>([])
  const loading = ref(false)
  const loaded = ref(false)

  const highestRole = computed<OrgRole | null>(() => {
    return organizations.value.reduce<OrgRole | null>((current, org) => {
      if (!current || ROLE_WEIGHT[org.role] > ROLE_WEIGHT[current]) return org.role
      return current
    }, null)
  })
  const hasTeams = computed(() => organizations.value.length > 0)
  const canManageTeam = computed(() => highestRole.value === 'owner' || highestRole.value === 'lead')
  const teamLabel = computed(() => {
    if (!hasTeams.value) return '团队中心'
    return canManageTeam.value ? '团队管理' : '团队协作'
  })

  function roleFor(orgId: string | null | undefined): OrgRole | null {
    if (!orgId) return null
    return organizations.value.find((org) => org.id === orgId)?.role ?? null
  }

  async function load(force = false) {
    if (USE_MOCK || (loaded.value && !force) || loading.value) return
    loading.value = true
    try {
      organizations.value = await orgApi.list()
      loaded.value = true
    } finally {
      loading.value = false
    }
  }

  return { organizations, loading, loaded, highestRole, hasTeams, canManageTeam, teamLabel, roleFor, load }
})
