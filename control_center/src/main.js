import 'frappe-ui/style.css';
import { createApp, reactive } from 'vue';
import App from './App.vue';
import './index.css';

import { FrappeUI } from 'frappe-ui';
import Auth from '../../../doppio/libs/controllers/auth';
import call from '../../../doppio/libs/controllers/call';
import socket from '../../../doppio/libs/controllers/socket';
import resourceManager from '../../../doppio/libs/resourceManager';
import router from './router';

const app = createApp(App);
const auth = reactive(new Auth());

// Plugins
app.use(router);
app.use(resourceManager);
app.use(FrappeUI, { call: true, socketio: false, resources: false });

// Global provides, components can inject these
app.provide('$auth', auth);
app.provide('$call', call);
app.provide('$socket', socket);

// Configure route guards
router.beforeEach(async (to, from, next) => {
	if (to.matched.some((record) => !record.meta.isLoginPage)) {
		if (!auth.isLoggedIn) {
			next({ name: 'Login', query: { route: to.path } });
		} else {
			next();
		}
	} else {
		if (auth.isLoggedIn) {
			next({ name: 'Home' });
		} else {
			next();
		}
	}
});

app.mount('#app');
