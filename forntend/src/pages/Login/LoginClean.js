import { ref, computed, nextTick } from 'vue';
import axios from 'axios';

export function useLoginForm() {
    const phone = ref('');
    const phoneError = ref(false);
    const showVerificationGroup = ref(false);
    const showEditPhoneLink = ref(false);
    const codeDigits = ref(['', '', '', '', '']);
    const codeError = ref(false);
    const codeHasVisualError = ref(false);
    const timer = ref(0);
    let timerInterval = null;

    const isCodeComplete = computed(() => {
        return codeDigits.value.every((d) => d !== '');
    });

    function filterPhoneInput(event) {
        const rawValue = event.target.value;
        const filtered = rawValue.replace(/[^0-9]/g, '');
        this.phone = filtered;
    }

    function validatePhone() {
        const regex = /^09\d{9}$/;
        return regex.test(phone.value);
    }

    async function handleSubmit() {
        if (!showVerificationGroup.value) {
            if (!validatePhone()) {
                phoneError.value = true;
                return;
            }

            phoneError.value = false;
            showVerificationGroup.value = true;
            showEditPhoneLink.value = true;
            startTimer();

            nextTick(() => {
                const firstInput = document.querySelector('.digit-input');
                if (firstInput) firstInput.focus();
            });
        } else {
            if (codeDigits.value.some((digit) => digit === '')) {
                codeError.value = 'لطفاً تمام کدها را وارد کنید';
                return;
            }

            const code = codeDigits.value.join('');

            if (code === '12345') {
                codeError.value = false;
                console.log('ورود موفق');
                window.location.href = '/signup';
            } else {
                codeError.value = 'لطفاً کد را به درستی وارد کنید';
                codeDigits.value = ['', '', '', '', ''];
                codeHasVisualError.value = true;

                nextTick(() => {
                    const firstInput = document.querySelector('.digit-input');
                    if (firstInput) firstInput.focus();
                });

                setTimeout(() => {
                    codeHasVisualError.value = false;
                    codeError.value = false;
                }, 3000);
            }
        }
    }

    function editPhoneNumber() {
        showVerificationGroup.value = false;
        showEditPhoneLink.value = false;
        timer.value = 0;
        clearInterval(timerInterval);
        codeDigits.value = ['', '', '', '', ''];
    }

    function startTimer() {
        timer.value = 60;
        timerInterval = setInterval(() => {
            timer.value--;
            if (timer.value <= 0) {
                clearInterval(timerInterval);
            }
        }, 1000);
    }

    function resendCode() {
        startTimer();
        codeDigits.value = ['', '', '', '', ''];
        nextTick(() => {
            const firstInput = document.querySelector('.digit-input');
            if (firstInput) firstInput.focus();
        });
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
        handleSubmit,
        editPhoneNumber,
        resendCode,
        focusNext,
        handleKeyDown,
        handlePaste,
        codeHasVisualError,
        filterPhoneInput,
    };
}
