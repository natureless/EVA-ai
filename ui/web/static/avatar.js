// EVA Avatar — detailed anime-style SVG character + zoom state machine + particle canvas
const AvatarController = {
  _state: "idle",
  _wrapper: null,
  _svg: null,
  _mouthPath: null,
  _leftEye: null,
  _rightEye: null,
  _particleCanvas: null,
  _particles: [],
  _particleRAF: null,
  _mouthTimer: null,

  init(containerId, canvasId) {
    this._wrapper = document.getElementById(containerId);
    this._particleCanvas = document.getElementById(canvasId);
    if (!this._wrapper) return;

    this._wrapper.innerHTML = this._buildSVG();
    this._svg = this._wrapper.querySelector("svg");
    this._mouthPath = this._svg.getElementById("eva-mouth");
    this._leftEye = this._svg.getElementById("eva-left-eye");
    this._rightEye = this._svg.getElementById("eva-right-eye");

    this._initParticles();
    this._particleLoop();
    this._setIdle();
  },

  // ═══════════════════════════════════════════════════════════
  // SVG CHARACTER — detailed anime sci-fi female
  // Silver-white hair with blue tips, red eyes, futuristic white/blue outfit
  // ═══════════════════════════════════════════════════════════
  _buildSVG() {
    return `
<svg viewBox="0 0 440 680" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <!-- Background energy glow -->
    <radialGradient id="bgGlow" cx="50%" cy="15%" r="45%">
      <stop offset="0%" stop-color="#2dd4bf" stop-opacity="0.25"/>
      <stop offset="50%" stop-color="#0d9488" stop-opacity="0.08"/>
      <stop offset="100%" stop-color="transparent" stop-opacity="0"/>
    </radialGradient>

    <!-- Hair: silver-white → blue tips -->
    <linearGradient id="hairMain" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#f0f0f8"/>
      <stop offset="40%" stop-color="#e8e8f4"/>
      <stop offset="70%" stop-color="#b8c8e8"/>
      <stop offset="90%" stop-color="#6888cc"/>
      <stop offset="100%" stop-color="#4068b8"/>
    </linearGradient>
    <linearGradient id="hairBlue" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#6688cc"/>
      <stop offset="50%" stop-color="#5078c0"/>
      <stop offset="100%" stop-color="#3860b0"/>
    </linearGradient>
    <linearGradient id="hairHighlight" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#ffffff"/>
      <stop offset="40%" stop-color="#f4f4fc"/>
      <stop offset="100%" stop-color="#c8d4f0"/>
    </linearGradient>

    <!-- Outfit gradients -->
    <linearGradient id="suitBase" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#f8fafc"/>
      <stop offset="50%" stop-color="#e8eef4"/>
      <stop offset="100%" stop-color="#d0d8e4"/>
    </linearGradient>
    <linearGradient id="suitDark" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0%" stop-color="#3a5a8c"/>
      <stop offset="50%" stop-color="#4a6ca0"/>
      <stop offset="100%" stop-color="#3a5a8c"/>
    </linearGradient>
    <linearGradient id="collarInner" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#305090"/>
      <stop offset="100%" stop-color="#1a3870"/>
    </linearGradient>

    <!-- Energy core -->
    <radialGradient id="coreGlow" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#2dd4bf" stop-opacity="0.9"/>
      <stop offset="40%" stop-color="#0d9488" stop-opacity="0.5"/>
      <stop offset="100%" stop-color="#0d9488" stop-opacity="0"/>
    </radialGradient>

    <!-- Eye gradient -->
    <radialGradient id="eyeRed" cx="50%" cy="40%" r="55%">
      <stop offset="0%" stop-color="#ff4466"/>
      <stop offset="60%" stop-color="#dd2244"/>
      <stop offset="100%" stop-color="#991133"/>
    </radialGradient>
  </defs>

  <!-- ═══ BACKGROUND GLOW ═══ -->
  <circle cx="220" cy="100" r="200" fill="url(#bgGlow)"/>

  <!-- ═══ HAIR — BACK LAYER (behind body, very long) ═══ -->
  <!-- Center back hair mass -->
  <path d="M175 80 Q165 160 160 260 Q155 360 165 440 Q175 480 200 510 Q220 530 240 510 Q260 480 270 440 Q280 360 275 260 Q270 160 265 80 Z"
        fill="url(#hairMain)" opacity="0.35"/>
  <!-- Back hair left flow -->
  <path d="M178 85 Q155 180 140 290 Q130 380 135 450 Q140 480 155 470 Q165 440 160 360 Q158 260 170 160 Z"
        fill="url(#hairMain)" opacity="0.3"/>
  <path d="M170 90 Q142 200 125 320 Q118 400 125 440 Q132 460 145 445 Q152 410 148 330 Q145 220 160 120 Z"
        fill="url(#hairBlue)" opacity="0.25"/>
  <!-- Back hair right flow -->
  <path d="M262 85 Q285 180 300 290 Q310 380 305 450 Q300 480 285 470 Q275 440 280 360 Q282 260 270 160 Z"
        fill="url(#hairMain)" opacity="0.3"/>
  <path d="M270 90 Q298 200 315 320 Q322 400 315 440 Q308 460 295 445 Q288 410 292 330 Q295 220 280 120 Z"
        fill="url(#hairBlue)" opacity="0.25"/>

  <!-- ═══ BODY / TORSO ═══ -->
  <!-- Main torso -->
  <path d="M178 230 Q170 260 165 320 Q162 380 168 420 Q175 445 195 455 Q215 458 220 455 Q245 445 252 420 Q258 380 255 320 Q250 260 242 230 Z"
        fill="url(#suitBase)" stroke="#bcc8d8" stroke-width="0.8"/>
  <!-- Waist -->
  <path d="M168 380 Q172 410 195 420 Q218 418 220 418 Q248 410 252 380 Q248 418 220 435 Q195 440 168 418 Z"
        fill="#d8e0ec"/>

  <!-- ═══ OUTFIT — blue geometric chest panels ═══ -->
  <!-- Center chest diamond / hex pattern -->
  <path d="M205 280 L220 265 L235 280 L235 305 L220 320 L205 305 Z"
        fill="url(#suitDark)" opacity="0.7"/>
  <path d="M210 285 L220 275 L230 285 L230 300 L220 310 L210 300 Z"
        fill="#5a80b8" opacity="0.5"/>
  <!-- Left chest accent line -->
  <path d="M185 270 L195 265 L195 315 L185 320 Z"
        fill="url(#suitDark)" opacity="0.4"/>
  <!-- Right chest accent line -->
  <path d="M235 270 L225 265 L225 315 L235 320 Z"
        fill="url(#suitDark)" opacity="0.4"/>
  <!-- Upper blue band -->
  <path d="M180 255 Q200 248 220 248 Q240 248 242 255 Q240 262 220 262 Q200 262 180 255 Z"
        fill="#4a6ca0" opacity="0.5"/>
  <!-- Lower chevron -->
  <path d="M195 340 L220 330 L245 340 L235 350 L220 348 L205 350 Z"
        fill="#3a5a8c" opacity="0.3"/>

  <!-- ═══ NECK ═══ -->
  <rect x="205" y="205" width="30" height="28" rx="4" fill="#f8ede4"/>

  <!-- ═══ COLLAR — high structured futuristic collar ═══ -->
  <!-- Collar back -->
  <path d="M195 195 Q190 175 195 160 Q200 152 210 150 Q220 150 230 152 Q235 160 240 175 Q245 195 242 205 Q238 195 230 188 Q220 184 210 188 Q202 195 195 205 Z"
        fill="url(#suitBase)" stroke="#a0b4cc" stroke-width="0.8"/>
  <!-- Collar inner blue -->
  <path d="M200 198 Q196 178 200 165 Q205 158 210 155 Q218 152 225 155 Q230 158 234 165 Q238 178 235 198 Q232 190 225 185 Q218 182 210 185 Q203 190 200 198 Z"
        fill="url(#collarInner)"/>
  <!-- Collar left wing -->
  <path d="M185 205 Q178 195 175 180 Q173 168 178 160 L185 162 Q182 172 184 188 Q186 198 190 205 Z"
        fill="url(#suitBase)" stroke="#a0b4cc" stroke-width="0.6"/>
  <!-- Collar right wing -->
  <path d="M235 205 Q242 195 245 180 Q247 168 242 160 L235 162 Q238 172 236 188 Q234 198 230 205 Z"
        fill="url(#suitBase)" stroke="#a0b4cc" stroke-width="0.6"/>
  <!-- Collar blue trim -->
  <path d="M198 195 Q193 175 196 162 Q200 156 206 154"
        fill="none" stroke="#5088cc" stroke-width="2" stroke-linecap="round" opacity="0.7"/>
  <path d="M222 195 Q227 175 224 162 Q220 156 214 154"
        fill="none" stroke="#5088cc" stroke-width="2" stroke-linecap="round" opacity="0.7"/>

  <!-- ═══ ARMS ═══ -->
  <!-- Left arm -->
  <path d="M180 235 Q165 275 155 320 Q150 350 153 370 Q156 378 160 370 Q162 350 165 320 Q172 275 185 250 Z"
        fill="url(#suitBase)" stroke="#bcc8d8" stroke-width="0.6"/>
  <!-- Left arm blue band -->
  <path d="M157 315 Q162 312 168 315 L167 325 Q162 322 157 325 Z"
        fill="#4a6ca0" opacity="0.5"/>
  <!-- Right arm -->
  <path d="M240 235 Q255 275 265 320 Q270 350 267 370 Q264 378 260 370 Q258 350 255 320 Q248 275 235 250 Z"
        fill="url(#suitBase)" stroke="#bcc8d8" stroke-width="0.6"/>
  <!-- Right arm blue band -->
  <path d="M263 315 Q258 312 252 315 L253 325 Q258 322 263 325 Z"
        fill="#4a6ca0" opacity="0.5"/>

  <!-- ═══ SHOULDERS — structural armor plates ═══ -->
  <path d="M175 235 Q165 228 168 218 Q175 214 185 216 Q188 224 185 235 Z"
        fill="#e8f0f8" stroke="#a0b4cc" stroke-width="0.7"/>
  <path d="M245 235 Q255 228 252 218 Q245 214 235 216 Q232 224 235 235 Z"
        fill="#e8f0f8" stroke="#a0b4cc" stroke-width="0.7"/>

  <!-- ═══ HEAD / FACE ═══ -->
  <!-- Skull base (under hair) -->
  <ellipse cx="220" cy="130" rx="50" ry="58" fill="#fdf2e8"/>

  <!-- Face shape — defined jaw -->
  <path d="M172 130 Q170 105 185 85 Q200 72 220 70 Q240 72 255 85 Q270 105 268 130 Q266 158 255 178 Q245 192 230 196 Q220 198 210 196 Q195 192 185 178 Q174 158 172 130 Z"
        fill="#fdf2e8" stroke="#e8d8c8" stroke-width="0.6"/>

  <!-- Face subtle shading — under chin -->
  <path d="M190 175 Q200 192 220 195 Q240 192 250 175 Q235 185 220 186 Q205 185 190 175 Z"
        fill="#e8d4c0" opacity="0.4"/>
  <!-- Cheek shading -->
  <ellipse cx="178" cy="140" rx="10" ry="14" fill="#f0ddd0" opacity="0.4"/>
  <ellipse cx="262" cy="140" rx="10" ry="14" fill="#f0ddd0" opacity="0.4"/>

  <!-- ═══ HAIR — CROWN & TOP ═══ -->
  <!-- Crown volume -->
  <path d="M175 100 Q170 60 185 35 Q200 18 220 16 Q240 18 255 35 Q270 60 265 100 Q268 82 260 65 Q248 42 235 35 Q220 30 205 35 Q192 42 180 65 Q172 82 175 100 Z"
        fill="url(#hairHighlight)"/>
  <!-- Crown top highlight sweep -->
  <path d="M195 45 Q210 28 220 26 Q230 28 245 45 Q235 35 220 33 Q205 35 195 45 Z"
        fill="#ffffff" opacity="0.7"/>

  <!-- ═══ HAIR — MAIN FRONT BANGS ═══ -->
  <!-- Left-swept main bang (covers forehead left to right) -->
  <path d="M172 105 Q168 70 175 42 Q182 22 200 18 Q210 17 218 20 L216 28 Q210 22 202 24 Q188 28 180 50 Q175 70 173 105 Z"
        fill="url(#hairHighlight)"/>
  <!-- Center bangs -->
  <path d="M175 105 Q178 60 190 35 Q198 22 210 20 L210 26 Q200 28 193 42 Q184 62 182 105 Z"
        fill="url(#hairMain)"/>
  <path d="M190 105 Q195 65 208 40 Q215 28 225 26 L224 32 Q216 34 210 48 Q202 68 198 105 Z"
        fill="#f0f0fc"/>
  <!-- Right bangs -->
  <path d="M230 105 Q235 70 242 48 Q250 32 262 28 L258 36 Q250 40 244 56 Q238 74 235 105 Z"
        fill="url(#hairMain)"/>
  <!-- Right side bang (longer) -->
  <path d="M248 105 Q258 75 268 52 Q275 38 282 32 L280 40 Q272 52 266 72 Q260 92 255 108 Z"
        fill="#e8e8f8"/>

  <!-- ═══ HAIR — SIDE STRANDS (face-framing) ═══ -->
  <!-- Left side strand 1 -->
  <path d="M170 110 Q160 110 152 123 Q145 138 143 158 Q142 170 144 178 L147 176 Q148 164 150 148 Q155 128 162 118 Z"
        fill="url(#hairHighlight)"/>
  <!-- Left side strand 2 (longer, past shoulder) -->
  <path d="M168 120 Q154 125 142 148 Q133 168 130 195 Q128 210 132 220 L136 218 Q134 205 136 188 Q140 165 148 145 Q156 130 166 126 Z"
        fill="url(#hairMain)"/>
  <path d="M165 130 Q150 138 138 162 Q129 182 126 210 Q124 225 128 235 L132 232 Q130 216 132 198 Q135 175 142 155 Q150 138 162 133 Z"
        fill="url(#hairBlue)" opacity="0.5"/>
  <!-- Right side strand 1 -->
  <path d="M270 110 Q278 112 286 126 Q293 142 294 160 Q295 168 293 176 L290 174 Q288 162 286 148 Q282 130 274 118 Z"
        fill="#f0f0fc"/>
  <!-- Right side strand 2 -->
  <path d="M272 125 Q284 132 294 156 Q301 175 302 200 Q302 215 298 225 L294 222 Q296 210 295 192 Q292 170 285 152 Q278 136 270 130 Z"
        fill="url(#hairMain)"/>

  <!-- ═══ HAIR — LONG FRONT STRANDS (over shoulders) ═══ -->
  <!-- Left over-shoulder strand -->
  <path d="M155 160 Q140 175 128 210 Q120 238 118 265 Q117 290 122 310 L128 308 Q126 285 127 262 Q130 235 138 212 Q148 185 158 168 Z"
        fill="url(#hairMain)"/>
  <path d="M150 180 Q135 200 125 235 Q118 260 116 290 Q115 315 120 335 L126 332 Q124 310 125 285 Q128 255 134 232 Q142 205 152 188 Z"
        fill="url(#hairBlue)" opacity="0.45"/>
  <!-- Right over-shoulder strand -->
  <path d="M285 160 Q300 175 312 210 Q320 238 322 265 Q323 290 318 310 L312 308 Q314 285 313 262 Q310 235 302 212 Q292 185 282 168 Z"
        fill="url(#hairMain)"/>
  <path d="M290 180 Q305 200 315 235 Q322 260 324 290 Q325 315 320 335 L314 332 Q316 310 315 285 Q312 255 306 232 Q298 205 288 188 Z"
        fill="url(#hairBlue)" opacity="0.45"/>

  <!-- ═══ EYEBROWS ═══ -->
  <path d="M183 99 Q192 93 204 95 Q210 96 214 98"
        fill="none" stroke="#886850" stroke-width="2" stroke-linecap="round" opacity="0.7"/>
  <path d="M257 99 Q248 93 236 95 Q230 96 226 98"
        fill="none" stroke="#886850" stroke-width="2" stroke-linecap="round" opacity="0.7"/>

  <!-- ═══ LEFT EYE (crimson/red, detailed) ═══ -->
  <g id="eva-left-eye" transform="translate(193, 118)">
    <!-- Eye shape — sharp anime upper lid -->
    <path d="M-16 0 Q-12 -8 -4 -10 Q4 -10 10 -6 Q14 -2 16 2 Q14 1 6 -2 Q-4 -4 -12 0 Q-15 1 -16 0 Z"
          fill="#221111" opacity="0.3"/>
    <!-- Lower lid line -->
    <path d="M-14 2 Q-8 8 0 10 Q8 10 14 6"
          fill="none" stroke="#886850" stroke-width="1.2" opacity="0.5"/>
    <!-- Top lash line -->
    <path d="M-16 0 Q-10 -6 0 -7 Q10 -6 16 0"
          fill="none" stroke="#332211" stroke-width="2.5" stroke-linecap="round"/>
    <!-- Upper lashes -->
    <path d="M-12 -3 L-13 -7 M-6 -7 L-7 -11 M0 -7 L0 -11 M6 -7 L7 -11 M12 -3 L13 -7"
          fill="none" stroke="#332211" stroke-width="1.5" stroke-linecap="round" opacity="0.8"/>
    <!-- Iris -->
    <circle cx="0" cy="2" r="11" fill="url(#eyeRed)"/>
    <!-- Pupil -->
    <circle cx="1" cy="3" r="6" fill="#220011"/>
    <!-- Iris detail ring -->
    <circle cx="0" cy="2" r="9" fill="none" stroke="#ff6688" stroke-width="0.6" opacity="0.5"/>
    <!-- Main catchlight -->
    <ellipse cx="3" cy="-2" rx="4" ry="3.5" fill="#ffffff" opacity="0.9"/>
    <!-- Secondary catchlight -->
    <circle cx="-4" cy="6" r="2" fill="#ffffff" opacity="0.5"/>
    <!-- Lower eyelid -->
    <path d="M-14 2 Q-6 12 6 11 Q12 10 14 7"
          fill="none" stroke="#886850" stroke-width="1" stroke-linecap="round" opacity="0.4"/>
    <!-- Eyelid (for blink animation) -->
    <path d="M-18 -2 Q-14 -14 0 -15 Q14 -14 18 -2 Q14 -10 0 -12 Q-14 -10 -18 -2 Z"
          fill="#fdf2e8" class="eva-eyelid"
          style="transform-origin:0 0;animation:avatar-blink-keyframes 5.5s infinite;"/>
  </g>

  <!-- ═══ RIGHT EYE ═══ -->
  <g id="eva-right-eye" transform="translate(247, 118)">
    <!-- Eye shape -->
    <path d="M16 0 Q12 -8 4 -10 Q-4 -10 -10 -6 Q-14 -2 -16 2 Q-14 1 -6 -2 Q4 -4 12 0 Q15 1 16 0 Z"
          fill="#221111" opacity="0.3"/>
    <!-- Lower lid line -->
    <path d="M14 2 Q8 8 0 10 Q-8 10 -14 6"
          fill="none" stroke="#886850" stroke-width="1.2" opacity="0.5"/>
    <!-- Top lash line -->
    <path d="M16 0 Q10 -6 0 -7 Q-10 -6 -16 0"
          fill="none" stroke="#332211" stroke-width="2.5" stroke-linecap="round"/>
    <!-- Upper lashes -->
    <path d="M12 -3 L13 -7 M6 -7 L7 -11 M0 -7 L0 -11 M-6 -7 L-7 -11 M-12 -3 L-13 -7"
          fill="none" stroke="#332211" stroke-width="1.5" stroke-linecap="round" opacity="0.8"/>
    <!-- Iris -->
    <circle cx="0" cy="2" r="11" fill="url(#eyeRed)"/>
    <!-- Pupil -->
    <circle cx="-1" cy="3" r="6" fill="#220011"/>
    <!-- Iris detail ring -->
    <circle cx="0" cy="2" r="9" fill="none" stroke="#ff6688" stroke-width="0.6" opacity="0.5"/>
    <!-- Main catchlight -->
    <ellipse cx="3" cy="-2" rx="4" ry="3.5" fill="#ffffff" opacity="0.9"/>
    <!-- Secondary catchlight -->
    <circle cx="-4" cy="6" r="2" fill="#ffffff" opacity="0.5"/>
    <!-- Lower eyelid -->
    <path d="M14 2 Q6 12 -6 11 Q-12 10 -14 7"
          fill="none" stroke="#886850" stroke-width="1" stroke-linecap="round" opacity="0.4"/>
    <!-- Eyelid (for blink animation) -->
    <path d="M18 -2 Q14 -14 0 -15 Q-14 -14 -18 -2 Q-14 -10 0 -12 Q14 -10 18 -2 Z"
          fill="#fdf2e8" class="eva-eyelid"
          style="transform-origin:0 0;animation:avatar-blink-keyframes 5.5s infinite;"/>
  </g>

  <!-- ═══ NOSE ═══ -->
  <path d="M220 125 L219 137 Q218 142 216 144"
        fill="none" stroke="#ccb8a0" stroke-width="1.2" stroke-linecap="round" opacity="0.5"/>
  <!-- Nostril hints -->
  <circle cx="212" cy="146" r="1.2" fill="#ccb8a0" opacity="0.3"/>
  <circle cx="228" cy="146" r="1.2" fill="#ccb8a0" opacity="0.3"/>

  <!-- ═══ MOUTH ═══ -->
  <path id="eva-mouth"
        d="M206 158 Q212 156 220 156 Q228 156 234 158"
        fill="none" stroke="#d08080" stroke-width="2" stroke-linecap="round" opacity="0.7"/>
  <!-- Lower lip shadow -->
  <path d="M210 160 Q220 165 230 160"
        fill="none" stroke="#d09080" stroke-width="1" stroke-linecap="round" opacity="0.35"/>

  <!-- ═══ OUTFIT — UPPER CHEST DETAILS ═══ -->
  <!-- Collarbone lines -->
  <path d="M195 218 Q210 225 220 226 Q230 225 245 218"
        fill="none" stroke="#c8b8a0" stroke-width="0.8" opacity="0.25"/>
  <!-- Sternum line -->
  <path d="M220 226 L220 260"
        fill="none" stroke="#bcc8d8" stroke-width="0.6" opacity="0.4"/>

  <!-- ═══ ENERGY CORE (chest center) ═══ -->
  <circle cx="220" cy="300" r="18" fill="url(#coreGlow)">
    <animate attributeName="r" values="16;20;16" dur="3.5s" repeatCount="indefinite"/>
    <animate attributeName="opacity" values="0.5;0.8;0.5" dur="3.5s" repeatCount="indefinite"/>
  </circle>
  <circle cx="220" cy="300" r="6" fill="#2dd4bf" opacity="0.8">
    <animate attributeName="opacity" values="0.6;1;0.6" dur="3.5s" repeatCount="indefinite"/>
  </circle>
  <circle cx="220" cy="300" r="3" fill="#ffffff" opacity="0.9">
    <animate attributeName="opacity" values="0.8;1;0.8" dur="3.5s" repeatCount="indefinite"/>
  </circle>
  <!-- Core ring -->
  <circle cx="220" cy="300" r="13" fill="none" stroke="#2dd4bf" stroke-width="1" opacity="0.4">
    <animate attributeName="r" values="12;15;12" dur="3.5s" repeatCount="indefinite"/>
    <animate attributeName="opacity" values="0.3;0.6;0.3" dur="3.5s" repeatCount="indefinite"/>
  </circle>

  <!-- ═══ HAIR — FRONT FLYAWAY STRANDS ═══ -->
  <path d="M160 75 Q152 70 148 58 Q146 50 150 45"
        fill="none" stroke="#f0f0fc" stroke-width="1.5" stroke-linecap="round" opacity="0.6"/>
  <path d="M168 62 Q162 55 160 44"
        fill="none" stroke="#e8e8f8" stroke-width="1.2" stroke-linecap="round" opacity="0.5"/>
  <path d="M272 70 Q278 62 282 52 Q284 46 282 40"
        fill="none" stroke="#f0f0fc" stroke-width="1.5" stroke-linecap="round" opacity="0.6"/>
  <path d="M265 58 Q270 48 274 38"
        fill="none" stroke="#e8e8f8" stroke-width="1.2" stroke-linecap="round" opacity="0.5"/>

  <!-- ═══ HAIR — HIGHLIGHT SWEEPS ON MAIN MASS ═══ -->
  <path d="M182 95 Q176 130 174 180 Q173 220 176 260"
        fill="none" stroke="#ffffff" stroke-width="2.5" stroke-linecap="round" opacity="0.18"/>
  <path d="M178 100 Q170 140 168 190 Q167 230 170 270"
        fill="none" stroke="#ffffff" stroke-width="1.8" stroke-linecap="round" opacity="0.12"/>
  <path d="M258 95 Q264 130 266 180 Q267 220 264 260"
        fill="none" stroke="#ffffff" stroke-width="2.5" stroke-linecap="round" opacity="0.18"/>
  <path d="M260 100 Q268 140 270 190 Q271 230 268 270"
        fill="none" stroke="#ffffff" stroke-width="1.8" stroke-linecap="round" opacity="0.12"/>

  <!-- ═══ EARPICE / HEADSET (right side) ═══ -->
  <g transform="translate(272, 128)">
    <path d="M0 0 Q8 2 12 8 Q14 12 12 16" fill="none" stroke="#6088b8" stroke-width="2.5" stroke-linecap="round" opacity="0.7"/>
    <circle cx="12" cy="16" r="4" fill="none" stroke="#6088b8" stroke-width="2" opacity="0.7"/>
    <circle cx="12" cy="16" r="1.5" fill="#2dd4bf" opacity="0.8">
      <animate attributeName="opacity" values="0.5;1;0.5" dur="2s" repeatCount="indefinite"/>
    </circle>
    <path d="M0 0 Q2 6 4 10" fill="none" stroke="#6088b8" stroke-width="1.5" stroke-linecap="round" opacity="0.5"/>
  </g>

  <!-- ═══ HAIR STRANDS OVER CHEST / SHOULDERS ═══ -->
  <path d="M155 210 Q142 230 135 260 Q130 280 132 295"
        fill="none" stroke="#d8e0f8" stroke-width="1.8" stroke-linecap="round" opacity="0.4"/>
  <path d="M285 210 Q298 230 305 260 Q310 280 308 295"
        fill="none" stroke="#d8e0f8" stroke-width="1.8" stroke-linecap="round" opacity="0.4"/>
</svg>`;
  },

  // ═══════════════════════════════════════════════════════════
  // STATE MACHINE
  // ═══════════════════════════════════════════════════════════

  _setIdle() {
    this._state = "idle";
    if (!this._wrapper) return;
    this._wrapper.style.transition = "transform var(--transition-zoom)";
    this._wrapper.style.transformOrigin = "50% 15%";
    this._wrapper.style.transform = "scale(2.5) translateY(0)";
    this._wrapper.classList.remove("listening", "responding");
    this._wrapper.classList.add("idle");
    this._svg && this._svg.classList.remove("responding");
    this._stopMouth();
    this._setParticles("calm");
    this._updateLabel("avatar.idle");
  },

  setListening() {
    if (this._state === "responding") return;
    this._state = "listening";
    if (!this._wrapper) return;
    this._wrapper.style.transition = "transform 400ms cubic-bezier(0.4, 0, 0.2, 1)";
    this._wrapper.style.transformOrigin = "50% 15%";
    this._wrapper.style.transform = "scale(2.8) translateY(2px)";
    this._wrapper.classList.remove("idle", "responding");
    this._wrapper.classList.add("listening");
    this._updateLabel("avatar.listening");
  },

  setResponding() {
    this._state = "responding";
    if (!this._wrapper) return;
    this._wrapper.style.transition = "none";
    this._wrapper.style.transformOrigin = "50% 45%";
    this._wrapper.offsetHeight;
    this._wrapper.style.transition = "transform var(--transition-zoom)";
    this._wrapper.style.transform = "scale(1.0) translateY(0)";
    this._wrapper.classList.remove("listening", "idle");
    this._wrapper.classList.add("responding");
    this._svg && this._svg.classList.add("responding");
    this._startMouth();
    this._setParticles("active");
    this._updateLabel("avatar.responding");
  },

  mouthCycle() {
    if (!this._mouthPath) return;
    const open = this._mouthPath.dataset.open === "1";
    if (open) {
      this._mouthPath.setAttribute("d", "M206 158 Q212 156 220 156 Q228 156 234 158");
      this._mouthPath.setAttribute("opacity", "0.7");
    } else {
      this._mouthPath.setAttribute("d", "M204 154 Q212 164 220 166 Q228 164 236 154");
      this._mouthPath.setAttribute("opacity", "0.8");
    }
    this._mouthPath.dataset.open = open ? "0" : "1";
  },

  _startMouth() {
    this._stopMouth();
    this._mouthTimer = setInterval(() => this.mouthCycle(), 280);
  },

  _stopMouth() {
    if (this._mouthTimer) { clearInterval(this._mouthTimer); this._mouthTimer = null; }
    if (this._mouthPath) {
      this._mouthPath.setAttribute("d", "M206 158 Q212 156 220 156 Q228 156 234 158");
      this._mouthPath.dataset.open = "0";
    }
  },

  _updateLabel(key) {
    const el = document.getElementById("avatarStateLabel");
    if (el && I18N) { el.textContent = I18N.t(key); }
  },

  // ═══════════════════════════════════════════════════════════
  // PARTICLE SYSTEM (Canvas)
  // ═══════════════════════════════════════════════════════════

  _initParticles() {
    const c = this._particleCanvas;
    if (!c) return;
    this._resizeCanvas();
    this._spawnParticles(10);
    window.addEventListener("resize", () => this._resizeCanvas());
  },

  _resizeCanvas() {
    const c = this._particleCanvas;
    if (!c || !c.parentElement) return;
    const rect = c.parentElement.getBoundingClientRect();
    c.width = rect.width;
    c.height = rect.height;
  },

  _spawnParticles(count) {
    const c = this._particleCanvas;
    if (!c) return;
    for (let i = 0; i < count; i++) {
      const hue = Math.random() < 0.6 ? "accent" : (Math.random() < 0.5 ? "teal" : "white");
      this._particles.push({
        x: Math.random() * c.width,
        y: c.height * 0.25 + Math.random() * c.height * 0.55,
        r: 0.8 + Math.random() * 2.8,
        speed: 0.3 + Math.random() * 0.8,
        drift: (Math.random() - 0.5) * 0.5,
        opacity: Math.random() * 0.5,
        hue,
        shape: Math.random() < 0.3 ? "diamond" : "circle",
        glow: Math.random() < 0.25,
      });
    }
  },

  _setParticles(mode) {
    const target = mode === "active" ? 40 : 10;
    const diff = target - this._particles.length;
    if (diff > 0) this._spawnParticles(diff);
    if (diff < 0) this._particles.length = target;
    this._particles.forEach(p => {
      p.speed = mode === "active" ? 0.6 + Math.random() * 1.5 : 0.3 + Math.random() * 0.8;
    });
  },

  _particleLoop() {
    const c = this._particleCanvas;
    if (!c) { this._particleRAF = requestAnimationFrame(() => this._particleLoop()); return; }
    const ctx = c.getContext("2d");
    const w = c.width, h = c.height;

    ctx.clearRect(0, 0, w, h);

    const style = getComputedStyle(document.documentElement);
    const accentColor = style.getPropertyValue("--accent").trim() || "#0f766e";
    const accentLight = style.getPropertyValue("--accent-light").trim() || "#ccfbf1";

    for (const p of this._particles) {
      p.y -= p.speed;
      p.x += p.drift;
      p.opacity -= 0.0015;
      if (p.y < -10 || p.opacity <= 0) {
        p.y = h * 0.25 + Math.random() * h * 0.55;
        p.x = Math.random() * w;
        p.opacity = 0.3 + Math.random() * 0.4;
      }

      const color = p.hue === "accent" ? accentColor
        : p.hue === "teal" ? "#2dd4bf"
        : accentLight;

      ctx.save();
      ctx.globalAlpha = p.opacity;

      if (p.glow) {
        ctx.shadowColor = color;
        ctx.shadowBlur = 6;
      }

      if (p.shape === "diamond") {
        ctx.beginPath();
        ctx.moveTo(p.x, p.y - p.r);
        ctx.lineTo(p.x + p.r * 0.7, p.y);
        ctx.lineTo(p.x, p.y + p.r);
        ctx.lineTo(p.x - p.r * 0.7, p.y);
        ctx.closePath();
        ctx.fillStyle = color;
        ctx.fill();
      } else {
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        ctx.fillStyle = color;
        ctx.fill();
      }

      ctx.restore();
    }

    this._particleRAF = requestAnimationFrame(() => this._particleLoop());
  },

  destroy() {
    if (this._particleRAF) cancelAnimationFrame(this._particleRAF);
    if (this._mouthTimer) clearInterval(this._mouthTimer);
  },
};
