<template>
  <Teleport to="body">
    <div class="announce-overlay" v-if="visible" @click.self="close">
      <div class="announce-panel">
        <div class="announce-scroll">
          <h2>ClipMind v1.0</h2>

          <p class="section-label">当前版本状态</p>
          <p>当前版本还比较早期，很多东西都在一边做一边改。如果你用着觉得哪里不顺，大概率不是你的问题——后面会越来越好的。</p>
          <p class="plan"><strong>接下来会做的：</strong>上云，让响应更快更稳；加更多剪辑能力；把细节再磨一磨。</p>

          <p class="section-label">目前能做到啥</p>
          <p>常规剪辑基本都覆盖了——多轨时间线编排、视频裁切拼接、智能场景分割、语音自动转字幕、配音合成、背景音乐匹配、节奏卡点、调色、变速、转场这些全都能做。你拿一堆素材丢进来，告诉AI想剪成什么样，它能从头到尾给你剪出一支完整的片子。</p>
          <p><strong>暂时做不了的：</strong>带有强烈个人风格的那种"剪辑味道"我们还做不出来，表情包类的效果也还没上。但如果你想用自己的音乐、自己收藏的表情包，直接往素材区上传就行，AI会帮你安排进去。</p>
          <p>我们做不到完美，但每一步做了什么、为什么这么做，都会直接摆在你面前。不满意的地方告诉AI就行，它会定位了改。</p>

          <p class="section-label">一点心里话</p>
          <p>其实这个软件的起点挺离谱的——就是吹了个牛。</p>
          <p>我本身就是个剪辑师，今年AI进化得实在太快了，从过年那会儿开始我几乎每天都在拿AI试新东西。上一份工作辞了之后手里还有点余粮，就一直窝在家里折腾。那时候Codex还没影呢，还是OpenClaw（龙虾）比较火的时候。我就这么一天天试，想知道AI的边界到底在哪。但大伙也知道，这东西哪有什么边界——几乎一天一个样，今天刚学会的Skill工作流，明天就过时了。</p>
          <p>后来想着先把研究放一放，去找工作。面试AI抽卡师的时候，面试官问我AI了解多少。我当时心想，我这几个月天天跟AI泡在一起，能不了解吗？结果越聊越上头，一个没刹住就说"我用AI做了个软件，AI自动化剪辑的"。</p>
          <p>牛吹大了。走的时候我还拍胸脯保证，说这软件肯定能剪出视频来。</p>
          <p>但大伙应该也清楚，再厉害的抽卡师，产出的东西也是需要剪辑师再去排版才能看的。AI漫剧、AI短剧，不管怎么生成，最后那一步还是得人来。吹出去的牛收不回来了，我就硬着头皮让人家HR把素材发过来，说我用软件给他剪。</p>
          <p>可我那时候手上其实啥都没有。</p>
          <p>后来我逼自己整了一个很粗糙的系统。自己试了试，确实能看出剪辑痕迹，但那剪出来的东西压根没法看。可牛都吹出去了——人嘛，不争馒头争口气。虽然到最后我都没脸再去联系那个HR，但这个软件我是真的一步一步做出来了。</p>
          <p>细节方面我自己觉得已经处理得还可以了，但也清楚还有很多不到位的地方。以后要是觉得哪儿不好，欢迎来找我反馈，看到了我就会改。哪怕剪出来的结果你不满意也没关系，告诉AI具体哪不对，它会定位了去改。</p>
          <p class="contact">📮 QQ：2217142796 &nbsp;·&nbsp; 微信：w1786854468</p>
          <p class="thanks">感谢你愿意在它还不太完美的时候，给一次机会。</p>
        </div>

        <button class="announce-x" @click="close">✕</button>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
const props = withDefaults(defineProps<{
  visible: boolean
}>(), {})

const emit = defineEmits<{
  close: []
}>()

function close() {
  // 记录展示次数
  const key = 'clipmind_announce_count'
  let count = parseInt(localStorage.getItem(key) || '0', 10)
  localStorage.setItem(key, String(count + 1))
  emit('close')
}
</script>

<style scoped>
.announce-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.7);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 2000;
  backdrop-filter: blur(6px);
}

.announce-panel {
  background: var(--surface-elevated, #28282E);
  border: 1px solid var(--border-card, rgba(255,255,255,0.11));
  border-radius: 14px;
  width: 520px;
  max-width: 90vw;
  max-height: 80vh;
  position: relative;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  box-shadow: var(--shadow-lg, 0 8px 40px rgba(0,0,0,0.5));
}

.announce-scroll {
  padding: 32px 28px 16px;
  overflow-y: auto;
  flex: 1;
}

.announce-scroll h2 {
  font-size: 22px;
  font-weight: 700;
  color: #FAFAFA;
  margin-bottom: 20px;
  text-align: center;
}

.announce-scroll p {
  font-size: 13.5px;
  line-height: 1.7;
  color: #D4D4D8;
  margin-bottom: 12px;
}

.section-label {
  font-size: 12px !important;
  font-weight: 600;
  color: #A78BFA !important;
  text-transform: uppercase;
  letter-spacing: 1px;
  margin-top: 20px !important;
  margin-bottom: 8px !important;
}

p.plan {
  background: var(--surface-overlay, #202026);
  padding: 12px 14px;
  border-radius: 8px;
  border-left: 3px solid #7C3AED;
}

p.thanks {
  margin-top: 16px !important;
  font-weight: 600;
  color: #C4B5FD !important;
  text-align: center;
  font-size: 14px !important;
}

p.contact {
  margin-top: 8px !important;
  padding: 10px 14px;
  background: var(--surface-overlay, #202026);
  border-radius: 8px;
  text-align: center;
  font-size: 13px !important;
  color: #A1A1AA !important;
  letter-spacing: 0.5px;
}

.announce-scroll strong {
  color: #E4E4E7;
}

.announce-x {
  position: absolute;
  top: 12px;
  right: 14px;
  width: 28px;
  height: 28px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: #6E6E7A;
  font-size: 16px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s;
}
.announce-x:hover {
  background: rgba(255,255,255,0.08);
  color: #E4E4E7;
}
</style>
