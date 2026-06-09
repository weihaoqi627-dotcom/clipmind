import { createRouter, createWebHashHistory } from 'vue-router'
import ChatPage from '../views/ChatPage.vue'
import PreviewPage from '../views/PreviewPage.vue'
import AccountPage from '../views/AccountPage.vue'

const routes = [
  { path: '/',        name: 'chat',    component: ChatPage },
  { path: '/preview', name: 'preview', component: PreviewPage },
  { path: '/account', name: 'account', component: AccountPage },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

export default router
