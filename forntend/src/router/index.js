import { createRouter, createWebHistory } from 'vue-router';
import NotFound from '@/pages/NotFound/NotFound.vue';

const routes = [
    // {
    //     path: '/',
    //     name: 'Home',
    //     component: () => import('../pages/Home/Home.vue'),
    //     meta: {
    //         title: 'صفحه اصلی',
    //     },
    // },
    // {
    //     path: '/login',
    //     name: 'Login',
    //     component: () => import('../pages/Login/Login.vue'),
    //     meta: {
    //         title: 'صفحه ورود',
    //     },
    // },
    // {
    //     path: '/signup',
    //     name: 'Signup',
    //     component: () => import('../pages/Login/Signup.vue'),
    //     meta: {
    //         title: 'صفحه ثبت نام',
    //     },
    // },
    {
        path: '/res/:bakery_id/:ticket_token',
        name: 'QueueStatus',
        component: () => import('../pages/Home/main.vue'),
        meta: {
            title: 'مشاهده نوبت',
        },
    },
    {
        path: '/:pathMatch(.*)*',
        name: 'NotFound',
        component: NotFound,
    },
];

const router = createRouter({
    history: createWebHistory(),
    routes,
});

// تنظیم عنوان تب به‌صورت پویا
router.beforeEach((to, from, next) => {
    document.title = to.meta.title || 'نون‌یار';
    next();
});

export default router;
