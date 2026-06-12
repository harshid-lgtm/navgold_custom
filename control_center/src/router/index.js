import { createRouter, createWebHistory } from 'vue-router';
import Home from '../views/Home.vue';
import authRoutes from './auth';

const routes = [
	{
		path: '/',
		name: 'Home',
		component: Home,
	},
	{
		path: '/data-readiness',
		name: 'DataReadiness',
		component: () => import('../views/reports/DataReadiness.vue'),
	},
	{
		path: '/pricing-push',
		name: 'PricingPushMonitor',
		component: () => import('../views/reports/PricingPushMonitor.vue'),
	},
	{
		path: '/below-min-sales',
		name: 'BelowMinSales',
		component: () => import('../views/reports/BelowMinSales.vue'),
	},
	{
		path: '/order-sync',
		name: 'OrderSyncHealth',
		component: () => import('../views/reports/OrderSyncHealth.vue'),
	},
	{
		path: '/supplier-readiness',
		name: 'SupplierReadiness',
		component: () => import('../views/reports/SupplierReadiness.vue'),
	},
	{
		path: '/push-readiness',
		name: 'PushReadiness',
		component: () => import('../views/reports/PushReadiness.vue'),
	},
	{
		path: '/emergency',
		name: 'Emergency',
		component: () => import('../views/Emergency.vue'),
	},
	...authRoutes,
];

const router = createRouter({
	history: createWebHistory('/control_center'),
	routes,
});

export default router;
