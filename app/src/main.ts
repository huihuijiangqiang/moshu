import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import { router } from './router'
import './styles/modernist.css'
import './styles/tokens.css'
import './styles/app.css'
import './styles/shell.css'

createApp(App).use(createPinia()).use(router).mount('#app')
