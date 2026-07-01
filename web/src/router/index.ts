import { createMemoryHistory, createRouter, createWebHistory, type RouterHistory } from 'vue-router'

export function createAppRouter(history: RouterHistory = createWebHistory(import.meta.env.BASE_URL)) {
  return createRouter({
    history,
    routes: [
      {
        path: '/',
        redirect: '/codex',
      },
      {
        path: '/login',
        name: 'login',
        component: () => import('../views/LoginView.vue'),
      },
      {
        path: '/codex/embed',
        name: 'codex-embed',
        component: () => import('../views/CodexEmbedView.vue'),
      },
      {
        path: '/codex',
        name: 'codex',
        component: () => import('../views/CodexView.vue'),
      },
    ],
  })
}

export function createTestRouter() {
  return createAppRouter(createMemoryHistory())
}

const router = createAppRouter()

export default router
