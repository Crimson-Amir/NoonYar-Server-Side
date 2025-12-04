<template>
    <div
        class="min-h-screen bg-slate-900 text-slate-100 flex justify-center items-center p-4 font-fa"
        dir="rtl"
    >
        <!-- پس‌زمینه -->
        <div
            class="fixed top-0 left-0 w-full h-full overflow-hidden -z-10 pointer-events-none"
        >
            <div
                class="absolute top-[-10%] right-[-10%] w-96 h-96 bg-purple-600/30 rounded-full blur-3xl opacity-50"
            ></div>
            <div
                class="absolute bottom-[-10%] left-[-10%] w-96 h-96 bg-emerald-600/30 rounded-full blur-3xl opacity-50"
            ></div>
        </div>
        <div
            class="w-full max-w-md bg-slate-800/60 backdrop-blur-xl border border-slate-700/50 shadow-2xl rounded-3xl overflow-hidden relative"
        >
            <!-- لودینگ -->
            <div
                v-if="isLoading"
                class="absolute inset-0 z-20 flex items-center justify-center bg-slate-900/80 backdrop-blur-sm"
            >
                <div
                    class="animate-spin rounded-full h-12 w-12 border-t-2 border-b-2 border-emerald-500"
                ></div>
            </div>
            <!-- 🔴 حالت ارور: تیکت پیدا نشد -->
            <div
                v-if="ticketNotFound"
                class="p-10 text-center flex flex-col items-center justify-center min-h-[400px]"
            >
                <div
                    class="w-20 h-20 rounded-full bg-red-500/20 flex items-center justify-center text-red-500 mb-6"
                >
                    <svg
                        xmlns="http://www.w3.org/2000/svg"
                        class="h-10 w-10"
                        fill="none"
                        viewBox="0 0 24 24"
                        stroke="currentColor"
                    >
                        <path
                            stroke-linecap="round"
                            stroke-linejoin="round"
                            stroke-width="2"
                            d="M6 18L18 6M6 6l12 12"
                        />
                    </svg>
                </div>
                <h3 class="text-2xl font-bold text-red-400 mb-2">
                    نوبت یافت نشد
                </h3>
                <p class="text-slate-400">
                    شماره نوبت یا کد نانوایی نامعتبر است.
                </p>
            </div>
            <!-- 🟢 حالت عادی: نمایش پنل نوبت -->
            <div v-else>
                <!-- بخش بالایی (دایره وضعیت) -->
                <div class="mb-6 p-8 text-center relative">
                    <h2
                        v-if="!isInWaitList && !isServed"
                        class="text-slate-300 text-xl font-medium mb-8 tracking-wide"
                    >
                        نوبت فعلی نانوایی:
                    </h2>
                    <h2
                        v-else-if="isInWaitList && !isServed"
                        class="text-emerald-400 text-2xl font-bold mb-8 tracking-wide animate-pulse"
                    >
                        نوبت شما رسید!
                    </h2>
                    <div
                        class="relative inline-flex justify-center items-center w-60 h-60"
                    >
                        <div
                            class="absolute inset-0 rounded-full border-3 border-slate-700"
                        ></div>
                        <div
                            class="absolute inset-0 rounded-full border-[3px] transition-all duration-500"
                            :class="statusRingClass"
                        ></div>
                        <div
                            class="flex flex-col items-center justify-center z-10 w-full px-4 text-center h-full"
                        >
                            <!-- نمایش عدد -->
                            <span
                                v-if="!isInWaitList && !isServed"
                                class="translate-y-[10%] text-8xl font-black bg-gradient-to-b from-white to-slate-400 bg-clip-text text-transparent drop-shadow-sm"
                            >
                                {{ currentTurn }}
                            </span>
                            <!-- نمایش پیام آماده -->
                            <div
                                v-else-if="isInWaitList"
                                class="flex flex-col items-center animate-pulse"
                            >
                                <span
                                    class="text-4xl font-black text-emerald-400 leading-tight drop-shadow-[0_0_10px_rgba(16,185,129,0.5)]"
                                >
                                    مراجعه<br />به نانوایی
                                </span>
                            </div>
                            <!-- نمایش پیام تحویل شد -->
                            <div
                                v-else-if="isServed"
                                class="flex flex-col items-center"
                            >
                                <svg
                                    xmlns="http://www.w3.org/2000/svg"
                                    class="h-16 w-16 text-blue-500 mb-2"
                                    fill="none"
                                    viewBox="0 0 24 24"
                                    stroke="currentColor"
                                >
                                    <path
                                        stroke-linecap="round"
                                        stroke-linejoin="round"
                                        stroke-width="2"
                                        d="M5 13l4 4L19 7"
                                    />
                                </svg>
                                <span
                                    class="text-2xl font-black text-blue-400 leading-tight"
                                >
                                    سفارش تحویل<br />داده شد
                                </span>
                            </div>
                        </div>
                    </div>
                    <!-- محل نمایش زمان بروزرسانی -->
                    <div
                        v-if="!isServed && lastUpdated"
                        class="mt-8 flex justify-center"
                    >
                        <div
                            class="relative group overflow-hidden flex items-center gap-2.5 px-4 py-2 bg-slate-900/40 backdrop-blur-md border border-slate-700/50 rounded-full shadow-lg shadow-black/20 transition-all duration-300 hover:bg-slate-800/50 hover:border-slate-600"
                        >
                            <span class="relative flex h-2.5 w-2.5">
                                <span
                                    v-if="isDataFresh"
                                    class="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"
                                ></span>
                                <span
                                    class="relative inline-flex rounded-full h-2.5 w-2.5 transition-colors duration-500"
                                    :class="
                                        isDataFresh
                                            ? 'bg-emerald-500'
                                            : 'bg-yellow-500'
                                    "
                                ></span>
                            </span>

                            <!-- متن زمان -->
                            <span
                                class="text-slate-300 font-medium tracking-wide opacity-90 dir-rtl"
                            >
                                {{ lastUpdateText }}
                            </span>

                            <!-- آیکون رفرش کوچک -->
                            <svg
                                xmlns="http://www.w3.org/2000/svg"
                                class="h-3.5 w-3.5 text-slate-500 transition-transform duration-700 group-hover:rotate-180"
                                fill="none"
                                viewBox="0 0 24 24"
                                stroke="currentColor"
                            >
                                <path
                                    stroke-linecap="round"
                                    stroke-linejoin="round"
                                    stroke-width="2"
                                    d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
                                />
                            </svg>
                        </div>
                    </div>
                </div>
                <!-- ⭐ بخش امتیازدهی (فقط وقتی تحویل شد نمایش داده می‌شود) ⭐ -->
                <div
                    v-if="isServed"
                    class="px-6 pb-10 text-center animate-fade-in"
                >
                    <div
                        class="bg-slate-700/30 rounded-2xl p-6 border border-slate-600/50"
                    >
                        <h3 class="text-lg font-bold text-slate-200 mb-2">
                            از نون‌یار راضی بودید؟
                        </h3>
                        <p class="text-slate-400 text-sm mb-6">
                            با ثبت امتیاز به بهبود کیفیت ما کمک کنید.
                        </p>
                        <div v-if="!ratingSubmitted">
                            <!-- بخش ستاره‌ها و اعداد -->
                            <div class="flex justify-center gap-4 mb-8 dir-ltr">
                                <div
                                    v-for="star in 5"
                                    :key="star"
                                    class="flex flex-col items-center gap-2 group cursor-pointer"
                                    @click="selectedRating = star"
                                    @mouseenter="hoveredStar = star"
                                    @mouseleave="hoveredStar = 0"
                                >
                                    <!-- آیکون ستاره -->
                                    <svg
                                        xmlns="http://www.w3.org/2000/svg"
                                        class="h-9 w-9 transition-all duration-200 transform group-active:scale-90"
                                        :class="
                                            hoveredStar >= star ||
                                            (!hoveredStar &&
                                                selectedRating >= star)
                                                ? 'text-yellow-400 drop-shadow-[0_0_8px_rgba(250,204,21,0.4)]'
                                                : 'text-slate-600'
                                        "
                                        fill="currentColor"
                                        viewBox="0 0 24 24"
                                    >
                                        <path
                                            d="M12 17.27L18.18 21l-1.64-7.03L22 9.24l-7.19-.61L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21z"
                                        />
                                    </svg>
                                    <!-- عدد زیر ستاره -->
                                    <span
                                        class="text-xs font-bold transition-colors"
                                        :class="
                                            hoveredStar >= star ||
                                            (!hoveredStar &&
                                                selectedRating >= star)
                                                ? 'text-slate-200'
                                                : 'text-slate-600'
                                        "
                                    >
                                        {{ star }}
                                    </span>
                                </div>
                            </div>
                            <!-- دکمه ثبت -->
                            <button
                                @click="submitRating"
                                :disabled="selectedRating === 0"
                                class="w-full py-3 rounded-xl font-bold transition-all duration-300 flex items-center justify-center gap-2"
                                :class="
                                    selectedRating > 0
                                        ? 'bg-emerald-600 text-white hover:bg-emerald-500 shadow-lg shadow-emerald-500/20 active:scale-[0.98]'
                                        : 'bg-slate-700 text-slate-500 cursor-not-allowed opacity-50'
                                "
                            >
                                <span>ثبت نظر</span>
                            </button>
                        </div>
                        <div
                            v-else
                            class="text-emerald-400 font-bold py-4 bg-emerald-500/10 rounded-xl border border-emerald-500/20"
                        >
                            نظر شما با موفقیت ثبت شد.
                        </div>
                    </div>
                </div>
                <!-- 📋 بخش جزئیات و صف (وقتی تحویل شد مخفی می‌شود) -->
                <div v-if="!isServed" class="px-6 space-y-6 pb-8">
                    <!-- کارت اصلی نوبت (شامل نوبت، زمان و جزئیات کشویی) -->
                    <div
                        class="bg-slate-700/40 rounded-2xl border border-slate-600/50 overflow-hidden transition-all duration-300"
                    >
                        <!-- بخش بالایی: اطلاعات نوبت و زمان -->
                        <div class="p-4 flex justify-around items-center">
                            <div class="flex flex-col items-center">
                                <span class="text-slate-400 mb-2"
                                    >نوبت شما</span
                                >
                                <span
                                    class="text-3xl font-bold"
                                    :class="
                                        isServed
                                            ? 'text-blue-400'
                                            : 'text-emerald-400'
                                    "
                                >
                                    {{ userTicketId }}
                                </span>
                            </div>
                            <div class="h-8 w-[1px] bg-slate-600"></div>
                            <div class="flex flex-col items-center">
                                <span class="text-slate-400 mb-4">
                                    <span v-if="isAccurateTime"
                                        >زمان دقیق تحویل</span
                                    >
                                    <span v-else>زمان تقریبی تحویل</span>
                                </span>
                                <span
                                    class="text-xl font-semibold dir-ltr"
                                    :class="
                                        (timeColorClass,
                                        isAccurateTime ? 'animate-pulse' : '')
                                    "
                                >
                                    {{ timeDisplay }}
                                </span>
                            </div>
                        </div>

                        <!-- بخش دکمه کشویی (فقط اگر نانی سفارش داده باشد نمایش داده می‌شود) -->
                        <div
                            v-if="
                                Object.values(userOrderedBreads).some(
                                    (c) => c > 0
                                )
                            "
                        >
                            <!-- دکمه تریگر -->
                            <button
                                @click="showOrderDetails = !showOrderDetails"
                                class="w-full flex items-center justify-center gap-2 py-2 bg-slate-800/50 hover:bg-slate-800 transition-colors border-t border-slate-600/30 text-xs text-slate-400"
                            >
                                <span>{{
                                    showOrderDetails
                                        ? 'بستن جزئیات سفارش'
                                        : 'مشاهده سفارش شما'
                                }}</span>
                                <svg
                                    xmlns="http://www.w3.org/2000/svg"
                                    class="h-3 w-3 transition-transform duration-300"
                                    :class="{ 'rotate-180': showOrderDetails }"
                                    fill="none"
                                    viewBox="0 0 24 24"
                                    stroke="currentColor"
                                >
                                    <path
                                        stroke-linecap="round"
                                        stroke-linejoin="round"
                                        stroke-width="2"
                                        d="M19 9l-7 7-7-7"
                                    />
                                </svg>
                            </button>

                            <!-- محتوای مخفی (لیست نان‌ها) -->
                            <div
                                class="bg-slate-900/30 overflow-hidden transition-all duration-300 ease-in-out"
                                :style="{
                                    maxHeight: showOrderDetails
                                        ? '200px'
                                        : '0px',
                                    opacity: showOrderDetails ? '1' : '0',
                                }"
                            >
                                <div
                                    class="p-4 flex flex-wrap justify-center gap-3"
                                >
                                    <template
                                        v-for="(
                                            count, type
                                        ) in userOrderedBreads"
                                        :key="type"
                                    >
                                        <div
                                            v-if="count > 0"
                                            class="flex items-center justify-between gap-3 bg-slate-800 px-3 py-1.5 rounded-lg border border-slate-700/50 shadow-sm min-w-[110px]"
                                        >
                                            <span
                                                class="text-slate-300 text-xs"
                                                >{{
                                                    breadLabels[type] ||
                                                    'نامشخص'
                                                }}</span
                                            >
                                            <div
                                                class="flex items-center gap-1"
                                            >
                                                <span
                                                    class="text-emerald-400 font-bold text-base"
                                                    >{{ count }}</span
                                                >
                                                <span
                                                    class="text-[10px] text-slate-500"
                                                    >عدد</span
                                                >
                                            </div>
                                        </div>
                                    </template>
                                </div>
                            </div>
                        </div>
                    </div>

                    <!-- دکمه دریافت پیامک -->
                    <button
                        @click="handleSmsClick"
                        class="w-full group relative overflow-hidden bg-emerald-600 hover:bg-emerald-500 text-white font-bold py-4 rounded-xl transition-all shadow-lg shadow-emerald-900/20 active:scale-[0.98]"
                    >
                        <!-- محتویات دکمه پیامک (بدون تغییر) -->
                        <span
                            class="relative z-10 flex items-center justify-center gap-2"
                        >
                            <svg
                                xmlns="http://www.w3.org/2000/svg"
                                class="h-5 w-5"
                                fill="none"
                                viewBox="0 0 24 24"
                                stroke="currentColor"
                            >
                                <path
                                    stroke-linecap="round"
                                    stroke-linejoin="round"
                                    stroke-width="2"
                                    d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9"
                                />
                            </svg>
                            دریافت پیامک اطلاع‌رسانی
                        </span>
                        <div
                            class="absolute inset-0 h-full w-full bg-gradient-to-r from-transparent via-white/20 to-transparent -translate-x-full group-hover:animate-shimmer"
                        ></div>
                    </button>
                    <button
                        @click="showQueue = !showQueue"
                        class="w-full flex items-center justify-center gap-2 text-slate-400 hover:text-emerald-400 transition-colors text-sm py-2"
                    >
                        <span>{{
                            showQueue ? 'بستن جزئیات صف' : 'مشاهده افراد در صف'
                        }}</span>
                        <svg
                            xmlns="http://www.w3.org/2000/svg"
                            class="h-4 w-4 transition-transform duration-300"
                            :class="{ 'rotate-180': showQueue }"
                            fill="none"
                            viewBox="0 0 24 24"
                            stroke="currentColor"
                        >
                            <path
                                stroke-linecap="round"
                                stroke-linejoin="round"
                                stroke-width="2"
                                d="M19 9l-7 7-7-7"
                            />
                        </svg>
                    </button>
                </div>

                <!-- لیست صف -->
                <div
                    v-if="!isServed"
                    class="bg-slate-900/50 border-t border-slate-700/50 transition-all duration-500 ease-in-out overflow-hidden"
                    :style="{ maxHeight: showQueue ? '400px' : '0px' }"
                >
                    <div
                        class="p-4 space-y-3 overflow-y-auto max-h-[400px] custom-scrollbar"
                    >
                        <transition-group name="list">
                            <div
                                v-for="person in activeQueueList"
                                :key="person.id"
                                class="flex items-center justify-between p-3 rounded-xl border transition-all"
                                :class="getQueueItemClass(person)"
                            >
                                <div class="flex items-center gap-3">
                                    <div
                                        class="w-12 h-12 rounded-full flex items-center justify-center font-bold text-xl"
                                        :class="
                                            person.id == currentTurn
                                                ? 'bg-emerald-500 text-white'
                                                : 'bg-slate-700 text-slate-400'
                                        "
                                    >
                                        {{ person.id }}
                                    </div>
                                    <div class="flex flex-col">
                                        <span
                                            class="font-medium"
                                            :class="
                                                person.id == userTicketId
                                                    ? 'text-white'
                                                    : 'text-slate-300'
                                            "
                                        >
                                            {{
                                                person.id == userTicketId
                                                    ? 'نوبت شما'
                                                    : `مشتری شماره ${person.id}`
                                            }}
                                        </span>
                                    </div>
                                </div>
                                <div
                                    class="flex items-center gap-2 bg-slate-800/80 px-3 py-1 rounded-lg"
                                >
                                    <span class="text-emerald-400 font-bold">{{
                                        person.breads
                                    }}</span>
                                    <span class="text-xs text-slate-500"
                                        >نان</span
                                    >
                                </div>
                            </div>
                        </transition-group>
                        <div
                            v-if="activeQueueList.length === 0 && !isLoading"
                            class="text-center text-slate-500 py-6 px-4"
                        >
                            <p class="mb-2">لیست صف خالی است.</p>
                            <p class="text-xs opacity-70">
                                نوبت شما گذشته است، یا هنوز کسی در صف ثبت نشده.
                            </p>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        <transition name="toast">
            <div
                v-if="showToast"
                class="fixed top-6 left-0 right-0 mx-auto w-max z-50 flex items-center gap-3 px-6 py-4 bg-slate-800/90 backdrop-blur-md border border-emerald-500/30 text-slate-100 rounded-2xl shadow-[0_0_10px_rgba(16,185,129,0.4)]"
            >
                <div
                    class="w-8 h-8 rounded-full bg-emerald-500/20 flex items-center justify-center text-emerald-400"
                >
                    <svg
                        xmlns="http://www.w3.org/2000/svg"
                        class="h-5 w-5"
                        fill="none"
                        viewBox="0 0 24 24"
                        stroke="currentColor"
                    >
                        <path
                            stroke-linecap="round"
                            stroke-linejoin="round"
                            stroke-width="2"
                            d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
                        />
                    </svg>
                </div>
                <div class="flex flex-col">
                    <span class="font-bold text-sm text-emerald-400"
                        >به‌زودی...</span
                    >
                    <span class="text-xs text-slate-300"
                        >این ویژگی در آپدیت بعدی فعال می‌شود.</span
                    >
                </div>
            </div>
        </transition>
    </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue';
import { useRoute } from 'vue-router';
import notificationUrl from '@/assets/Ting.mp3';

// 🛠 تنظیمات ورودی
const route = useRoute();
const API_BASE = 'http://noonyar.freebyte.shop';

// State
const currentTurn = ref(null);
const userTicketId = ref(null);
const userTicketToken = ref('');
const showQueue = ref(false);
const showOrderDetails = ref(false);
const isLoading = ref(true);
const ticketNotFound = ref(false);

// وضعیت نوبت
const isInWaitList = ref(false);
const isServed = ref(false);
const isCooking = ref(true);
const isAccurateTime = ref(false);

// داده‌ها
const queueSummaryMap = ref({});
const peopleAhead = ref(0);
const initialQueueSize = ref(0);

// زمان
const timeDisplay = ref('...');

// ابزارها
const showToast = ref(false);
let toastTimer = null;
let notificationSound = null;
let pollingInterval = null;

// ⭐ State برای امتیازدهی
const hoveredStar = ref(0);
const ratingSubmitted = ref(false);
const selectedRating = ref(0);

// 🥖 متغیرهای جدید برای سفارش نان
const userOrderedBreads = ref({});
const breadLabels = {
    1: 'نان ساده',
    2: 'نان کنجدی',
    3: 'نان بزرگ کنجدی',
};

// اضافه کردن متغیرهای زمان آپدیت صفحه
const lastUpdated = ref(null); // زمان آخرین دریافت اطلاعات
const now = ref(new Date()); // زمان جاری برای محاسبه اختلاف

// ----------------------------------------------------------------------------
// 🕒 زمان‌بندی
// ----------------------------------------------------------------------------

// محاسبه متن زمان گذشته شده
const lastUpdateText = computed(() => {
    if (!lastUpdated.value) return '';

    const diffMs = now.value - lastUpdated.value;
    const diffMins = Math.floor(diffMs / 60000);

    if (diffMins < 1) {
        return 'بروزرسانی: همین الان';
    }

    const persianMin = diffMins.toLocaleString('fa-IR');
    return `بروزرسانی: ${persianMin} دقیقه پیش`;
});

const isDataFresh = computed(() => {
    if (!lastUpdated.value) return false;
    const diffMs = now.value - lastUpdated.value;
    // اگر کمتر از ۶۰ ثانیه گذشته باشد، دیتا "تازه" محسوب می‌شود
    return diffMs < 60000;
});

const formatTimeInterval = (seconds) => {
    if (seconds === null || seconds === undefined) return 'نامشخص';
    if (seconds <= 0) return 'همین حالا';

    const now = new Date();
    const targetTime = new Date(now.getTime() + seconds * 1000);

    let hour = targetTime.getHours();
    let minute = targetTime.getMinutes();

    let startMinute = Math.floor(minute / 10) * 10;
    let endMinute = startMinute + 10;
    let startHour = hour;
    let endHour = hour;

    if (endMinute === 60) {
        endMinute = 0;
        endHour += 1;
        if (endHour === 24) endHour = 0;
    }

    const f = (n) => n.toString().padStart(2, '0');
    return `${f(startHour)}:${f(startMinute)} - ${f(endHour)}:${f(endMinute)}`;
};

const formatExactTime = (seconds) => {
    if (seconds === null || seconds === undefined) return '-';
    if (seconds <= 0) return 'همین حالا';

    const now = new Date();
    const targetTime = new Date(now.getTime() + seconds * 1000);

    // رند کردن دقیقه به بالا
    if (targetTime.getSeconds() > 0 || targetTime.getMilliseconds() > 0) {
        targetTime.setMinutes(targetTime.getMinutes() + 1);
    }
    // ثانیه نمایش داده نمی‌شود پس عملاً مهم نیست اما برای اطمینان صفر می‌کنیم
    targetTime.setSeconds(0);

    const f = (n) => n.toString().padStart(2, '0');
    return `${f(targetTime.getHours())}:${f(targetTime.getMinutes())}`;
};

// ----------------------------------------------------------------------------
// 🔄 دریافت داده‌ها
// ----------------------------------------------------------------------------
const fetchAllData = async () => {
    const bakeryId = route.params.bakery_id;
    const ticketToken = route.params.ticket_token;

    if (!bakeryId || !ticketToken) return;

    userTicketToken.value = ticketToken;

    try {
        // -------------------------------------------------
        // 1. دریافت وضعیت نوبت (Main Status)
        // -------------------------------------------------
        const resResponse = await fetch(
            `${API_BASE}/res/${bakeryId}/${ticketToken}`
        );
        const resData = await resResponse.json();

        userOrderedBreads.value = resData.user_breads || {};

        // 🛠 نرمال‌سازی متغیر detail
        const detailBody = resData.detail;
        const detailMsg =
            typeof detailBody === 'object' && detailBody !== null
                ? detailBody.message
                : detailBody;

        // 🔴 بررسی ارور "پیدا نشد"
        if (detailMsg === 'Customer not found for token') {
            ticketNotFound.value = true;
            isLoading.value = false;

            // ✅ توقف تایمر چون تیکت نامعتبر است
            if (pollingInterval) clearInterval(pollingInterval);
            return;
        }

        ticketNotFound.value = false;

        let fetchedUserTicketId = null;
        let fetchedServerTurn = null;

        // 🟡 بررسی حالت ویت لیست
        if (detailMsg === 'ticket is in wait list') {
            isInWaitList.value = true;
            isServed.value = false;
            isCooking.value = false;
            timeDisplay.value = 'آماده تحویل';

            if (typeof detailBody === 'object') {
                fetchedUserTicketId = detailBody.ticket_id;
                fetchedServerTurn = detailBody.current_ticket_id;
            }
        }
        // 🔵 بررسی حالت تحویل شده (Served)
        else if (detailMsg === 'ticket is served') {
            isInWaitList.value = false;
            isServed.value = true;
            isCooking.value = false;
            timeDisplay.value = 'تحویل داده شد';

            fetchedUserTicketId = resData.ticket_id;
            fetchedServerTurn = resData.current_ticket_id;

            // ✅ توقف تایمر چون سفارش تحویل شده و کار تمام است
            if (pollingInterval) clearInterval(pollingInterval);
        }
        // ⚪ حالت عادی (Cooking)
        else {
            isInWaitList.value = false;
            isServed.value = false;
            isCooking.value = true;

            fetchedUserTicketId = resData.ticket_id;
            fetchedServerTurn = resData.current_ticket_id;
            isAccurateTime.value = !!resData.accurate_time;

            if (
                fetchedUserTicketId &&
                fetchedUserTicketId == fetchedServerTurn
            ) {
                isInWaitList.value = true;
                timeDisplay.value = 'آماده تحویل';
            } else {
                if (isAccurateTime.value) {
                    timeDisplay.value = formatExactTime(resData.wait_until);
                } else {
                    const totalWaitSeconds =
                        resData.wait_until + resData.empty_slot_time_avg;
                    timeDisplay.value = formatTimeInterval(totalWaitSeconds);
                }
            }
        }

        // 🟢 آپدیت کردن Ref های اصلی (فقط یک بار انجام شود)
        if (fetchedUserTicketId !== null && fetchedUserTicketId !== undefined) {
            userTicketId.value = fetchedUserTicketId;
        }

        currentTurn.value =
            fetchedServerTurn !== null && fetchedServerTurn !== undefined
                ? fetchedServerTurn
                : 0;

        // -------------------------------------------------
        // 2. دریافت لیست صف (Queue List)
        // فقط اگر هنوز سرویس تمام نشده و تیکت معتبر است دریافت شود
        // -------------------------------------------------
        if (!isServed.value && !ticketNotFound.value) {
            const summaryResponse = await fetch(
                `${API_BASE}/queue_until_ticket_summary/${bakeryId}/${ticketToken}`
            );

            if (summaryResponse.ok) {
                const summaryData = await summaryResponse.json();

                queueSummaryMap.value =
                    summaryData.tickets_and_their_bread_count || {};

                peopleAhead.value =
                    summaryData.people_in_queue_until_this_ticket || 0;

                if (initialQueueSize.value === 0 && peopleAhead.value > 0) {
                    initialQueueSize.value = peopleAhead.value;
                }
            }
        }
        lastUpdated.value = new Date();
    } catch (error) {
        console.error('API Error:', error);
    } finally {
        isLoading.value = false;
    }
};

// ----------------------------------------------------------------------------
// 📊 استایل‌ها و محاسبات
// ----------------------------------------------------------------------------
const activeQueueList = computed(() => {
    const list = [];
    for (const [tid, breads] of Object.entries(queueSummaryMap.value)) {
        // تبدیل tid به عدد برای مقایسه صحیح
        const ticketNum = parseInt(tid);

        // نمایش نوبت‌های بزرگتر یا مساوی نوبت فعلی نانوایی
        if (currentTurn.value === 0 || ticketNum >= currentTurn.value) {
            list.push({ id: ticketNum, breads: breads });
        }
    }
    return list.sort((a, b) => a.id - b.id);
});

const statusRingClass = computed(() => {
    if (isServed.value)
        return 'border-blue-500 shadow-[0_0_35px_rgba(59,130,246,0.6)]';
    if (isInWaitList.value)
        return 'border-emerald-500 shadow-[0_0_35px_rgba(16,185,129,0.6)] animate-pulse';
    return 'border-amber-500 shadow-[0_0_20px_rgba(245,158,11,0.4)]';
});

const timeColorClass = computed(() => {
    if (isServed.value) return 'text-blue-400';
    if (isInWaitList.value) return 'text-emerald-400';
    return 'text-white';
});

const getQueueItemClass = (person) => {
    if (person.id === userTicketId.value) {
        return 'bg-emerald-900/20 border-emerald-500/30 shadow-[0_0_10px_rgba(16,185,129,0.1)]';
    }
    if (person.id === currentTurn.value) {
        return 'bg-slate-700/30 border-slate-600 translate-x-1';
    }
    return 'bg-transparent border-transparent hover:bg-slate-800/50';
};

// ----------------------------------------------------------------------------
// 🔔 Event Handlers
// ----------------------------------------------------------------------------
const handleSmsClick = () => {
    if (toastTimer) clearTimeout(toastTimer);
    showToast.value = false;
    setTimeout(() => {
        showToast.value = true;
        toastTimer = setTimeout(() => {
            showToast.value = false;
        }, 3000);
    }, 50);
};

// ⭐ هندلر ثبت امتیاز
const submitRating = async () => {
    if (selectedRating.value === 0 || ratingSubmitted.value) return;
    try {
        await fetch(`http://noonyar.ir/rate/${selectedRating.value}`);
        ratingSubmitted.value = true;
    } catch (error) {
        console.error('Error submitting rating:', error);
        ratingSubmitted.value = true;
    }
};

watch(isInWaitList, (newVal) => {
    if (newVal === true) {
        try {
            if (notificationSound) {
                notificationSound.currentTime = 0;
                notificationSound.play().catch(() => {});
            }
        } catch (e) {}
    }
});

onMounted(() => {
    try {
        notificationSound = new Audio(notificationUrl);
    } catch (e) {}

    fetchAllData();
    pollingInterval = setInterval(fetchAllData, 5000);

    // ✅ یک تایمر جداگانه برای آپدیت کردن زمان جاری (هر ۱۰ ثانیه)
    // این باعث می‌شود حتی اگر اینترنت قطع باشد، متن "۵ دقیقه پیش" به "۶ دقیقه پیش" تغییر کند
    setInterval(() => {
        now.value = new Date();
    }, 10000);
});

onUnmounted(() => {
    if (pollingInterval) clearInterval(pollingInterval);
});
</script>

<style>
@import url('https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn-font@v33.003/dist/font-face.css');

.font-fa {
    font-family: 'Vazirmatn', sans-serif;
}

.dir-ltr {
    direction: ltr;
}

@keyframes shimmer {
    100% {
        transform: translateX(100%);
    }
}

.animate-shimmer {
    animation: shimmer 2s infinite;
}

@keyframes pulse-fast {
    0%,
    100% {
        opacity: 0.3;
    }
    50% {
        opacity: 0.7;
    }
}

.animate-pulse-fast {
    animation: pulse-fast 2s cubic-bezier(0.4, 0, 0.6, 1) infinite;
}

.animate-fade-in {
    animation: fadeIn 0.5s ease-out forwards;
}

@keyframes fadeIn {
    from {
        opacity: 0;
        transform: translateY(10px);
    }
    to {
        opacity: 1;
        transform: translateY(0);
    }
}

.list-enter-active,
.list-leave-active {
    transition: all 0.4s ease;
}

.list-enter-from,
.list-leave-to {
    opacity: 0;
    transform: translateX(-20px);
}

.toast-enter-active,
.toast-leave-active {
    transition: all 0.5s cubic-bezier(0.68, -0.55, 0.27, 1.55);
}

.toast-enter-from,
.toast-leave-to {
    opacity: 0;
    transform: translateY(-100px);
}

.custom-scrollbar::-webkit-scrollbar {
    width: 4px;
}

.custom-scrollbar::-webkit-scrollbar-track {
    background: rgba(255, 255, 255, 0.05);
}

.custom-scrollbar::-webkit-scrollbar-thumb {
    background: rgba(255, 255, 255, 0.2);
    border-radius: 10px;
}
</style>
