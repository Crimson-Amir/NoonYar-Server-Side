<template>
    <div class="min-h-screen flex items-center justify-center bg-[#1e1e1e]">
        <div
            class="bg-[#2d2d2d] text-white rounded-[15px] shadow-[0_8px_24px_rgba(0,0,0,0.2)] p-10 w-full max-w-sm"
        >
            <h2 class="text-2xl text-center mb-8">ورود / ثبت نام</h2>

            <form @submit.prevent="handleSubmit" class="space-y-6" novalidate>
                <!-- شماره تلفن -->
                <div class="relative" dir="rtl">
                    <label
                        for="phone"
                        class="text-sm text-[#b3b3b3] text-right"
                    >
                        تلفن همراه (جهت ارسال کد تایید)
                    </label>

                    <span
                        v-if="showEditPhoneLink"
                        @click="editPhoneNumber"
                        class="text-[#4caf50] text-base cursor-pointer mr-2"
                    >
                        ویرایش
                    </span>

                    <input
                        type="tel"
                        id="phone"
                        v-model="phone"
                        :disabled="showVerificationGroup"
                        placeholder="مثال: 09123456789"
                        required
                        autocomplete="tel"
                        class="w-full px-4 py-3 text-center mt-5 bg-[#3d3d3d] border-2 border-[#4d4d4d] text-white text-base rounded-lg focus:outline-none focus:ring-0 focus:border-[#4caf50] focus:bg-[#454545] placeholder:text-center placeholder:text-[#888] placeholder:opacity-70 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                    />

                    <span
                        v-if="phoneError"
                        class="block mt-4 text-center text-sm text-[#ff6b6b]"
                    >
                        شماره باید با ۰۹ شروع شده و ۱۱ رقم باشد
                    </span>
                </div>

                <!-- کد تایید -->
                <div v-if="showVerificationGroup" class="space-y-4">
                    <label class="block text-sm text-[#b3b3b3] text-right"
                        >کد تأیید پیامک شده</label
                    >

                    <div class="flex justify-between gap-2">
                        <input
                            v-for="(digit, index) in codeDigits"
                            :key="index"
                            type="text"
                            maxlength="1"
                            inputmode="numeric"
                            v-model="codeDigits[index]"
                            @input="focusNext(index, $event)"
                            @keydown="handleKeyDown(index, $event)"
                            @paste="handlePaste"
                            class="digit-input w-12 h-12 text-center text-white text-xl rounded-lg focus:outline-none transition-colors bg-[#3d3d3d]"
                            :class="[
                                'border-2',
                                codeHasVisualError
                                    ? 'border-[#ff6b6b]'
                                    : 'border-[#4d4d4d] focus:border-[#4caf50]',
                            ]"
                        />
                    </div>

                    <span
                        v-if="codeError"
                        class="block mt-4 text-center text-sm text-[#ff6b6b]"
                    >
                        {{ codeError }}
                    </span>

                    <!-- تایمر و دکمه ارسال مجدد -->
                    <div class="text-center text-sm text-[#b3b3b3] mt-4">
                        <div v-if="timer > 0">
                            ارسال مجدد کد تا <span>{{ timer }}</span> ثانیه دیگر
                        </div>
                        <button
                            v-else
                            type="button"
                            @click="resendCode"
                            class="mt-2 text-[#4caf50] focus:outline-none cursor-pointer"
                        >
                            ارسال مجدد کد تایید
                        </button>
                    </div>
                </div>

                <!-- دکمه ورود -->
                <button
                    type="submit"
                    :disabled="
                        (showVerificationGroup && !isCodeComplete) || isLoading
                    "
                    class="w-full py-3 text-white text-xl bg-[#4caf50] rounded-lg transition-colors relative overflow-hidden focus:outline-none hover:bg-[#45a049] disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:bg-[#4caf50]"
                >
                    <span v-if="isLoading">در حال بررسی...</span>
                    <span v-else>ورود</span>
                </button>
            </form>

            <div class="mt-6 text-center text-sm text-[#b3b3b3]">
                نیاز به راهنمایی دارید؟
                <a href="http://localhost:5173/" class="text-[#4caf50] ml-2"
                    >اینجا کلیک کنید</a
                >
            </div>
        </div>
    </div>
</template>

<script setup>
import { useLoginForm } from '../../components/useLoginForm';

const {
    phone,
    phoneError,
    showVerificationGroup,
    showEditPhoneLink,
    codeDigits,
    codeError,
    timer,
    isCodeComplete,
    isLoading,
    handleSubmit,
    editPhoneNumber,
    resendCode,
    focusNext,
    handleKeyDown,
    handlePaste,
    codeHasVisualError,
} = useLoginForm();
</script>
