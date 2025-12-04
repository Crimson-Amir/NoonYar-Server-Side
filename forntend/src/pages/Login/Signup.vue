<template>
    <div
        class="min-h-screen flex items-center justify-center bg-[#1e1e1e] relative overflow-hidden"
    >
        <!-- Notification -->
        <transition name="notification-fade">
            <div
                v-if="notification.show"
                :class="[
                    'fixed top-6 left-1/2 transform -translate-x-1/2 z-50 px-5 py-4 w-[90%] max-w-sm shadow-lg flex items-start justify-between gap-4',
                    notification.type === 'error'
                        ? 'bg-red-600'
                        : 'bg-green-600',
                    'text-white rounded-md',
                ]"
            >
                <span class="text-sm">{{ notification.message }}</span>
                <button
                    @click="closeNotification"
                    class="text-white text-xl leading-none hover:opacity-70"
                >
                    &times;
                </button>
            </div>
        </transition>

        <div
            class="bg-[#2d2d2d] text-white rounded-[15px] shadow-[0_8px_24px_rgba(0,0,0,0.2)] p-10 w-full max-w-sm"
        >
            <h2 class="text-2xl text-center mb-8">تکمیل اطلاعات ثبت نام</h2>

            <form @submit.prevent="handleSignup" class="space-y-6">
                <div>
                    <label
                        :class="[
                            'block text-sm mb-2 text-right',
                            errors.firstName
                                ? 'text-red-500'
                                : 'text-[#b3b3b3]',
                        ]"
                        >* نام</label
                    >
                    <input
                        v-model="firstName"
                        @input="clearError('firstName')"
                        type="text"
                        :class="[
                            'w-full px-4 py-3 bg-[#3d3d3d] text-white text-base rounded-lg border-2 focus:outline-none',
                            errors.firstName
                                ? 'border-red-500 focus:border-red-500'
                                : 'border-[#4d4d4d] focus:border-[#4caf50]',
                        ]"
                    />
                </div>

                <div>
                    <label
                        :class="[
                            'block text-sm mb-2 text-right',
                            errors.lastName ? 'text-red-500' : 'text-[#b3b3b3]',
                        ]"
                        >* نام خانوادگی</label
                    >
                    <input
                        v-model="lastName"
                        @input="clearError('lastName')"
                        type="text"
                        :class="[
                            'w-full px-4 py-3 bg-[#3d3d3d] text-white text-base rounded-lg border-2 focus:outline-none',
                            errors.lastName
                                ? 'border-red-500 focus:border-red-500'
                                : 'border-[#4d4d4d] focus:border-[#4caf50]',
                        ]"
                    />
                </div>

                <div>
                    <label
                        :class="[
                            'block text-sm mb-2 text-right',
                            errors.email ? 'text-red-500' : 'text-[#b3b3b3]',
                        ]"
                        >ایمیل (اختیاری)</label
                    >
                    <input
                        v-model="email"
                        @input="clearError('email')"
                        type="text"
                        :class="[
                            'w-full px-4 py-3 bg-[#3d3d3d] text-white text-base rounded-lg border-2 focus:outline-none',
                            errors.email
                                ? 'border-red-500 focus:border-red-500'
                                : 'border-[#4d4d4d] focus:border-[#4caf50]',
                        ]"
                    />
                    <p
                        v-if="errors.email"
                        class="text-red-500 text-sm mt-2 text-right"
                    >
                        لطفا ایمیل خود را به شکل صحیح وارد کنید.
                    </p>
                </div>

                <button
                    type="submit"
                    :disabled="isLoading"
                    class="w-full py-3 bg-[#4caf50] hover:bg-[#45a049] text-white text-xl rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                    <span v-if="isLoading">در حال ثبت نام...</span>
                    <span v-else>ثبت نام</span>
                </button>
            </form>
        </div>
    </div>
</template>

<script setup>
import { ref, onMounted } from 'vue';
import axios from 'axios';

// آدرس پایه API بک‌اند شما
const API_BASE_URL = 'http://127.0.0.1:8000/api/accounts';

const firstName = ref('');
const lastName = ref('');
const email = ref('');
const isLoading = ref(false);

const errors = ref({
    firstName: false,
    lastName: false,
    email: false,
});

const notification = ref({
    show: false,
    message: '',
    type: 'error',
});

const timeoutId = ref(null); // برای مدیریت تایمر نوتیفیکیشن

// در ابتدای بارگذاری صفحه، چک می‌کنیم که توکن موقت وجود داشته باشد
onMounted(() => {
    const regToken = localStorage.getItem('registration_token');
    if (!regToken) {
        showNotification(
            'دسترسی نامعتبر. لطفاً ابتدا شماره خود را تایید کنید.',
            'error'
        );
        setTimeout(() => {
            window.location.href = '/'; // بازگشت به صفحه ورود
        }, 3000);
    }
});

function showNotification(msg, type = 'error') {
    notification.value.message = msg;
    notification.value.type = type;
    notification.value.show = true;

    if (timeoutId.value) {
        clearTimeout(timeoutId.value);
    }

    timeoutId.value = setTimeout(() => {
        notification.value.show = false;
        timeoutId.value = null;
    }, 5000);
}

function closeNotification() {
    notification.value.show = false;
    if (timeoutId.value) {
        clearTimeout(timeoutId.value);
        timeoutId.value = null;
    }
}

function validateForm() {
    let hasError = false;
    // ریست کردن خطاها قبل از هر بار اعتبارسنجی
    errors.value = { firstName: false, lastName: false, email: false };

    if (!firstName.value.trim()) {
        errors.value.firstName = true;
        hasError = true;
    }

    if (!lastName.value.trim()) {
        errors.value.lastName = true;
        hasError = true;
    }

    // ایمیل اختیاری است، اما اگر وارد شد باید معتبر باشد
    if (email.value && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.value)) {
        errors.value.email = true;
        hasError = true;
    }
    return !hasError;
}

// این تنها تابع handleSignup است که باید وجود داشته باشد
async function handleSignup() {
    if (!validateForm()) {
        showNotification('لطفاً خطاهای فرم را برطرف کنید.', 'error');
        return;
    }

    const regToken = localStorage.getItem('registration_token');
    if (!regToken) {
        showNotification(
            'توکن ثبت‌نام یافت نشد. لطفاً به صفحه ورود بازگردید.',
            'error'
        );
        return;
    }

    isLoading.value = true;

    try {
        const response = await axios.post(`${API_BASE_URL}/register/`, {
            first_name: firstName.value,
            last_name: lastName.value,
            email: email.value,
            registration_token: regToken,
        });

        showNotification(
            'ثبت نام با موفقیت انجام شد. در حال انتقال...',
            'success'
        );

        localStorage.setItem('access_token', response.data.tokens.access);
        localStorage.setItem('refresh_token', response.data.tokens.refresh);

        localStorage.removeItem('registration_token');
        localStorage.removeItem('phone_for_signup');

        setTimeout(() => {
            window.location.href = '/dashboard'; // یا هر آدرس دیگری برای بعد از ورود
        }, 2000);
    } catch (error) {
        const errorData = error.response?.data;
        let errorMessage = 'خطایی در هنگام ثبت نام رخ داد.';
        if (errorData) {
            if (errorData.email) errorMessage = `ایمیل: ${errorData.email[0]}`;
            else if (errorData.error) errorMessage = errorData.error;
            else if (typeof errorData === 'object' && errorData !== null) {
                // نمایش اولین خطای دریافتی از سرور
                const firstErrorKey = Object.keys(errorData)[0];
                errorMessage = `${firstErrorKey}: ${errorData[firstErrorKey][0]}`;
            }
        }
        showNotification(errorMessage, 'error');
        console.error('Signup error:', errorData || error.message);
    } finally {
        isLoading.value = false;
    }
}

function clearError(field) {
    if (errors.value[field]) {
        errors.value[field] = false;
    }
}
</script>
