<template>
    <div
        class="min-h-screen bg-slate-950 text-slate-100 flex flex-col justify-center items-center p-4 font-fa relative overflow-hidden perspective-container"
        dir="rtl"
    >
        <!-- 🌑 نویز پس‌زمینه برای حس بافت دیوار -->
        <div
            class="fixed inset-0 opacity-[0.03] pointer-events-none z-0"
            style="
                background-image: url('https://grainy-gradients.vercel.app/noise.svg');
            "
        ></div>

        <!-- 💡 نور محیطی بالای صفحه -->
        <div
            class="absolute top-0 left-1/2 -translate-x-1/2 w-[600px] h-[200px] bg-emerald-500/20 blur-[120px] rounded-full pointer-events-none"
        ></div>

        <!-- محتوای اصلی -->
        <div class="relative z-10 flex flex-col items-center">
            <!-- 404 با افکت نئون و پرش -->
            <div class="relative mb-10">
                <h1
                    class="text-[180px] md:text-[250px] font-black leading-none tracking-tighter text-transparent bg-clip-text bg-gradient-to-b from-emerald-100 to-emerald-600 select-none neon-text animate-flicker"
                >
                    404
                </h1>

                <!-- افکت بازتاب روی زمین -->
                <div
                    class="absolute top-[85%] left-0 right-0 transform scale-y-[-1] opacity-20 blur-sm mask-gradient select-none pointer-events-none"
                >
                    <h1
                        class="text-[180px] md:text-[250px] font-black leading-none tracking-tighter text-emerald-500"
                    >
                        404
                    </h1>
                </div>
            </div>

            <!-- پیام خطا -->
            <div
                class="text-center space-y-6 animate-fade-in max-w-md mx-auto relative"
            >
                <div
                    class="h-1 w-20 bg-emerald-500/50 mx-auto rounded-full shadow-[0_0_15px_rgba(16,185,129,1)]"
                ></div>

                <h2
                    class="text-3xl md:text-4xl font-bold text-slate-200 drop-shadow-lg"
                >
                    نوبت پیدا نشد...
                </h2>
                <p class="text-slate-400 text-lg leading-relaxed">
                    به نظر می‌رسه نوبت یا صفحه‌ای که دنبالش هستی وجود نداره!
                </p>
            </div>

            <!-- دکمه‌ها با استایل متفاوت (Outline Neon) -->
            <div
                class="mt-12 flex flex-col sm:flex-row gap-4 w-full max-w-sm animate-fade-in delay-200"
            ></div>
        </div>

        <!-- ذرات معلق در هوا -->
        <div class="fixed inset-0 pointer-events-none">
            <div
                v-for="n in 15"
                :key="n"
                class="particle"
                :style="getParticleStyle(n)"
            ></div>
        </div>
    </div>
</template>

<script setup>
const getParticleStyle = (n) => {
    const size = Math.random() * 3 + 1;
    return {
        top: `${Math.random() * 100}%`,
        left: `${Math.random() * 100}%`,
        width: `${size}px`,
        height: `${size}px`,
        animationDelay: `${Math.random() * 5}s`,
        animationDuration: `${Math.random() * 10 + 10}s`,
        opacity: Math.random() * 0.5,
    };
};
</script>

<style scoped>
@import url('https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn-font@v33.003/dist/font-face.css');

.font-fa {
    font-family: 'Vazirmatn', sans-serif;
}

.perspective-container {
    perspective: 1000px;
}

/* افکت سایه نئونی */
.neon-text {
    text-shadow: 0 0 40px rgba(16, 185, 129, 0.3),
        0 0 80px rgba(16, 185, 129, 0.1);
}

/* ماسک برای محو کردن انعکاس */
.mask-gradient {
    mask-image: linear-gradient(to bottom, rgba(0, 0, 0, 0.8), transparent);
    -webkit-mask-image: linear-gradient(
        to bottom,
        rgba(0, 0, 0, 0.8),
        transparent
    );
}

/* انیمیشن ورود نرم */
@keyframes fadeIn {
    from {
        opacity: 0;
        transform: translateY(20px);
    }
    to {
        opacity: 1;
        transform: translateY(0);
    }
}

.animate-fade-in {
    animation: fadeIn 1s ease-out forwards;
    opacity: 0;
}

.delay-200 {
    animation-delay: 0.3s;
}

/* ذرات معلق */
.particle {
    position: absolute;
    background: rgba(16, 185, 129, 0.3);
    border-radius: 50%;
    pointer-events: none;
    animation: floatUp linear infinite;
}

@keyframes floatUp {
    0% {
        transform: translateY(0) translateX(0);
        opacity: 0;
    }
    20% {
        opacity: 0.5;
    }
    100% {
        transform: translateY(-100vh) translateX(20px);
        opacity: 0;
    }
}
</style>
