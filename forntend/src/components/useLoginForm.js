import { ref, computed, nextTick } from 'vue';
import axios from 'axios'; // مطمئن شوید axios نصب است: npm install axios

// آدرس پایه API بک‌اند شما
const API_BASE_URL = 'http://127.0.0.1:8000/api/accounts';

export function useLoginForm() {
    // --- State Refs ---
    const phone = ref('');
    const phoneError = ref(''); // حالا متن خطا را نگه می‌دارد
    const showVerificationGroup = ref(false);
    const showEditPhoneLink = ref(false);
    const codeDigits = ref(['', '', '', '', '']);
    const codeError = ref(''); // حالا متن خطا را نگه می‌دارد
    const codeHasVisualError = ref(false);
    const timer = ref(0);
    const isLoading = ref(false); // برای مدیریت وضعیت بارگذاری
    let timerInterval = null;

    // --- Computed Properties ---
    const isCodeComplete = computed(() => {
        return codeDigits.value.every((d) => d && d.match(/^\d$/));
    });

    // --- Helper Functions ---
    function validatePhone() {
        const regex = /^09\d{9}$/;
        return regex.test(phone.value);
    }

    function startTimer() {
        if (timerInterval) clearInterval(timerInterval);
        timer.value = 120; // زمان را به ۲ دقیقه افزایش می‌دهیم
        timerInterval = setInterval(() => {
            timer.value--;
            if (timer.value <= 0) {
                clearInterval(timerInterval);
            }
        }, 1000);
    }

    function resetCodeInputs() {
        codeDigits.value = ['', '', '', '', ''];
        codeError.value = '';
        codeHasVisualError.value = false;
        nextTick(() => {
            const firstInput = document.querySelector('.digit-input');
            if (firstInput) firstInput.focus();
        });
    }

    // --- API Call Functions ---
    async function sendVerificationCode() {
        if (!validatePhone()) {
            phoneError.value = 'شماره باید با ۰۹ شروع شده و ۱۱ رقم باشد';
            return false;
        }
        phoneError.value = '';
        isLoading.value = true;

        try {
            await axios.post(`${API_BASE_URL}/send-code/`, {
                phone_number: phone.value,
            });
            showVerificationGroup.value = true;
            showEditPhoneLink.value = true;
            startTimer();
            resetCodeInputs();
            return true;
        } catch (error) {
            phoneError.value =
                'خطایی در ارسال کد رخ داد. لطفاً دوباره تلاش کنید.';
            console.error(
                'Send code error:',
                error.response?.data || error.message
            );
            return false;
        } finally {
            isLoading.value = false;
        }
    }

    // --- Event Handlers ---
    async function handleSubmit() {
        // مرحله ۱: ارسال شماره تلفن
        if (!showVerificationGroup.value) {
            await sendVerificationCode();
        }
        // مرحله ۲: تایید کد
        else {
            if (!isCodeComplete.value) {
                codeError.value = 'لطفاً تمام خانه‌ها را پر کنید.';
                return;
            }
            isLoading.value = true;
            codeError.value = '';

            const code = codeDigits.value.join('');

            try {
                const response = await axios.post(
                    `${API_BASE_URL}/verify-code/`,
                    {
                        phone_number: phone.value,
                        code: code,
                    }
                );

                // بررسی پاسخ سرور
                if (response.data.is_new_user) {
                    // کاربر جدید است، توکن موقت ثبت نام را ذخیره کن و به صفحه ثبت نام برو
                    localStorage.setItem(
                        'registration_token',
                        response.data.registration_token
                    );
                    // شماره تلفن را هم ذخیره می‌کنیم تا در صفحه بعد شاید لازم شود
                    localStorage.setItem('phone_for_signup', phone.value);
                    window.location.href = '/signup'; // آدرس صفحه ثبت‌نام شما
                } else {
                    // کاربر قدیمی است، توکن‌های ورود را ذخیره کن و به داشبورد برو
                    localStorage.setItem(
                        'access_token',
                        response.data.tokens.access
                    );
                    localStorage.setItem(
                        'refresh_token',
                        response.data.tokens.refresh
                    );
                    window.location.href = '/dashboard'; // آدرس صفحه داشبورد یا صفحه اصلی
                }
            } catch (error) {
                const errorMessage =
                    error.response?.data?.error ||
                    'کد وارد شده صحیح نیست یا منقضی شده.';
                codeError.value = errorMessage;
                codeHasVisualError.value = true;
                setTimeout(() => {
                    codeHasVisualError.value = false;
                }, 3000);
                console.error(
                    'Verify code error:',
                    error.response?.data || error.message
                );
            } finally {
                isLoading.value = false;
            }
        }
    }

    function editPhoneNumber() {
        showVerificationGroup.value = false;
        showEditPhoneLink.value = false;
        if (timerInterval) clearInterval(timerInterval);
        timer.value = 0;
        resetCodeInputs();
    }

    async function resendCode() {
        if (timer.value > 0) return;
        await sendVerificationCode();
    }

    function focusNext(index, event) {
        const value = event.target.value;
        const inputs = document.querySelectorAll('.digit-input');

        if (value.length > 1) {
            const digits = value.replace(/\D/g, '').slice(0, 5).split('');
            digits.forEach((digit, i) => {
                codeDigits.value[i] = digit;
            });
            nextTick(() => {
                const nextIndex = Math.min(
                    digits.length,
                    codeDigits.value.length - 1
                );
                inputs[nextIndex].focus();
            });
            return;
        }

        if (value && index < codeDigits.value.length - 1) {
            nextTick(() => {
                inputs[index + 1].focus();
            });
        }
    }

    function handleKeyDown(index, event) {
        const inputs = document.querySelectorAll('.digit-input');

        if (event.key === 'Backspace') {
            if (codeDigits.value[index] === '') {
                if (index > 0) {
                    event.preventDefault();
                    codeDigits.value[index - 1] = '';
                    nextTick(() => {
                        inputs[index - 1].focus();
                    });
                }
            }
        }
    }

    function handlePaste(event) {
        event.preventDefault();

        const paste = event.clipboardData
            .getData('text')
            .replace(/\D/g, '')
            .slice(0, codeDigits.value.length);
        const digits = paste.split('');

        digits.forEach((digit, i) => {
            codeDigits.value[i] = digit;
        });

        nextTick(() => {
            const inputs = document.querySelectorAll('.digit-input');
            if (inputs[digits.length - 1]) {
                inputs[digits.length - 1].focus();
            }
        });
    }

    return {
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
    };
}
