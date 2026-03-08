// ==========================
// ===== STATE ==============
// ==========================
let habits = JSON.parse(localStorage.getItem('nh_habits')) || [];

// ==========================
// ===== INIT ===============
// ==========================
function init() {
  setHeaderDate();
  renderAll();
  renderHeatmap();
}

// ==========================
// ===== HEADER DATE ========
// ==========================
function setHeaderDate() {
  const now = new Date();
  const options = { weekday:'long', year:'numeric', month:'long', day:'numeric' };
  document.getElementById('headerDate').textContent = now.toLocaleDateString('en-US', options);
}

// ==========================
// ===== RENDER ALL =========
// ==========================
function renderAll() {
  renderHabits();
  renderProgress();
  updateInsights();
}

// ==========================
// ===== ADD HABIT MODAL ====
// ==========================
function openModal() {
  const overlay = document.getElementById('modalOverlay');
  overlay.classList.add('open');
  setTimeout(() => document.getElementById('modalHabitName').focus(), 100);
}

function closeModal() {
  const overlay = document.getElementById('modalOverlay');
  overlay.classList.remove('open');

  document.getElementById('modalHabitName').value = '';
  document.getElementById('modalTarget').value = '';
  document.getElementById('modalUnit').value = '';
  document.getElementById('modalHabitType').value = 'static';
  document.getElementById('targetFieldWrap').style.display = 'none';
}

function toggleTargetField() {
  const type = document.getElementById('modalHabitType').value;
  document.getElementById('targetFieldWrap').style.display = type === 'dynamic' ? 'flex' : 'none';
}

// ==========================
// ===== ADD HABIT =========
// ==========================
function addHabitFromModal() {
  const text = document.getElementById('modalHabitName').value.trim();
  if (!text) return alert("Enter a habit name!");

  const type = document.getElementById('modalHabitType').value;
  const target = parseFloat(document.getElementById('modalTarget').value) || 0;
  const unit = document.getElementById('modalUnit').value.trim();

  const habit = {
    text,
    type,
    completed: false,
    currentValue: 0,
    targetValue: type === "dynamic" ? target || 1 : null,
    unit: type === "dynamic" ? unit : "",
    created: new Date().toISOString(),
    lastUpdated: null,
    streak: 0
  };

  habits.push(habit);
  saveHabits();
  renderAll();
  renderHeatmap();
  closeModal();
}

// ==========================
// ===== SAVE HABITS ========
// ==========================
function saveHabits() {
  const today = new Date();

  habits.forEach(habit => {
    if (habit.completed && habit.lastUpdated) {
      const last = new Date(habit.lastUpdated);
      const diff = Math.floor((today - last)/(1000*60*60*24));
      if(diff === 1) habit.streak = (habit.streak || 0) + 1;
      else if(diff > 1) habit.streak = 0;
    }
  });

  localStorage.setItem('nh_habits', JSON.stringify(habits));
}

// ==========================
// ===== RENDER HABITS ======
// ==========================
function renderHabits() {
  const list = document.getElementById('habitList');
  const empty = document.getElementById('emptyState');
  const countEl = document.getElementById('habitCount');

  list.innerHTML = '';
  countEl.textContent = habits.length + (habits.length === 1 ? ' habit' : ' habits');

  if(habits.length === 0){
    empty.style.display = 'block';
    return;
  }
  empty.style.display = 'none';

  habits.forEach((habit,i) => {
    const li = document.createElement('li');
    li.className = 'habit-item';

    // STATIC HABIT TOGGLE
    const check = document.createElement('div');
    check.className = 'habit-check' + (habit.completed?' done':'');
    check.innerHTML = habit.completed?'✓':'';
    if(habit.type==='static') check.onclick = ()=> toggleStatic(i);

    // HABIT INFO
    const info = document.createElement('div');
    info.className = 'habit-info';
    const name = document.createElement('div');
    name.className = 'habit-name' + (habit.completed?' done':'');
    name.textContent = habit.text;

    const meta = document.createElement('div');
    meta.className = 'habit-meta';
    const typeBadge = document.createElement('span');
    typeBadge.className = 'type-badge ' + (habit.type==='dynamic'?'type-dynamic':'type-static');
    typeBadge.textContent = habit.type;

    const streak = document.createElement('span');
    streak.className = 'streak-badge';
    streak.innerHTML = '🔥 ' + (habit.streak||0) + ' day streak';

    meta.appendChild(typeBadge);
    meta.appendChild(streak);
    info.appendChild(name);
    info.appendChild(meta);

    // DYNAMIC HABIT CONTROLS
    let controls = null;
    if(habit.type==='dynamic'){
      controls = document.createElement('div');
      controls.className = 'dynamic-controls';

      const inp = document.createElement('input');
      inp.type='number';
      inp.className='dynamic-input';
      inp.value = habit.currentValue||0;
      inp.min=0;
      inp.max=habit.targetValue||999;

      inp.oninput = e => {
        habit.currentValue = parseFloat(e.target.value) || 0;
        habit.completed = habit.currentValue >= habit.targetValue;
        saveHabits();
        renderAll();
      };

      const target = document.createElement('span');
      target.className='dynamic-target';
      target.textContent = '/ '+(habit.targetValue||'?') + (habit.unit?' '+habit.unit:'');

      const miniProg = document.createElement('div');
      miniProg.className='mini-progress';
      const fill = document.createElement('div');
      fill.className='mini-progress-fill';
      fill.style.width = Math.min(100, Math.round(((habit.currentValue||0)/(habit.targetValue||1))*100))+'%';
      miniProg.appendChild(fill);

      controls.appendChild(inp);
      controls.appendChild(target);
      controls.appendChild(miniProg);
    }

    // DELETE BUTTON
    const del = document.createElement('button');
    del.className='delete-btn';
    del.innerHTML='×';
    del.title='Remove habit';
    del.onclick = ()=> {
      habits.splice(i,1);
      saveHabits();
      renderAll();
      renderHeatmap();
    };

    li.appendChild(check);
    li.appendChild(info);
    if(controls) li.appendChild(controls);
    li.appendChild(del);
    list.appendChild(li);
  });
}

// ==========================
// ===== TOGGLE STATIC ======
// ==========================
function toggleStatic(i){
  habits[i].completed = !habits[i].completed;
  habits[i].lastUpdated = new Date().toISOString();
  saveHabits();
  renderAll();
}

// ==========================
// ===== PROGRESS BAR =======
// ==========================
function renderProgress() {
  if(!habits.length){
    document.getElementById('progressFill').style.width='0%';
    document.getElementById('progressPct').textContent='0%';
    document.getElementById('completedMiniList').innerHTML='';
    document.getElementById('pendingMiniList').innerHTML='';
    return;
  }

  let totalPct = 0;
  habits.forEach(h => {
    if(h.type==='static') totalPct += h.completed?100:0;
    else totalPct += Math.min(100, ((h.currentValue||0)/(h.targetValue||1))*100);
  });

  const avg = Math.round(totalPct/habits.length);
  document.getElementById('progressFill').style.width = avg+'%';
  document.getElementById('progressPct').textContent = avg+'%';

  renderMiniList('completedMiniList', habits.filter(h=>h.completed), true);
  renderMiniList('pendingMiniList', habits.filter(h=>!h.completed), false);
}

function renderMiniList(id,list,done){
  const el = document.getElementById(id);
  el.innerHTML='';
  if(list.length===0){
    el.innerHTML = `<li class="mini-habit" style="color:var(--muted);font-size:12px;font-style:italic;">None</li>`;
    return;
  }
  list.forEach(h=>{
    const li = document.createElement('li');
    li.className='mini-habit';
    const dot = document.createElement('span');
    dot.className='mini-dot '+(done?'dot-done':(h.type==='dynamic'?'dot-dynamic':'dot-pending'));
    const name = document.createElement('span');
    name.textContent = h.text;
    li.appendChild(dot);
    li.appendChild(name);
    el.appendChild(li);
  });
}

// ==========================
// ===== HEATMAP ============
// ==========================
function renderHeatmap(){
  const grid = document.getElementById('heatmapGrid');
  grid.innerHTML='';
  const days = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
  const data = JSON.parse(localStorage.getItem('nh_heatmap')) || [80,60,40,90,70,50,75];

  days.forEach((day,i)=>{
    const col = document.createElement('div');
    col.className='heatmap-day';

    const label = document.createElement('div');
    label.className='heatmap-day-name';
    label.textContent=day;

    const barWrap = document.createElement('div');
    barWrap.className='heatmap-bar-wrap';
    const fill = document.createElement('div');
    fill.className='heatmap-bar-fill';
    fill.style.width='0%';
    barWrap.appendChild(fill);
    setTimeout(()=>{fill.style.width=data[i]+'%';}, 100+i*60);

    const pct = document.createElement('div');
    pct.className='heatmap-pct';
    pct.textContent=data[i]+'%';

    col.appendChild(label);
    col.appendChild(barWrap);
    col.appendChild(pct);
    grid.appendChild(col);
  });
}

// ==========================
// ===== AI INSIGHTS =======
// ==========================
const insightTemplates = [
  h => `You've completed <strong>${h.filter(x=>x.completed).length}</strong> of <strong>${h.length}</strong> habits today. Keep pushing!`,
  h => {
    const best = h.reduce((a,b)=>(b.streak||0)>(a.streak||0)?b:a,h[0]);
    return best?`Your strongest streak is <strong>"${best.text}"</strong> at 🔥 <strong>${best.streak||0} days</strong>.`:'Start tracking to build streaks!';
  },
  ()=> 'Try scheduling your habits in the morning for better completion rates.',
  h=>{
    const dynamic = h.filter(x=>x.type==='dynamic');
    if(!dynamic.length) return 'Add dynamic habits to track quantitative goals like reading or exercise.';
    const near = dynamic.filter(x=>(x.currentValue||0)/(x.targetValue||1)>=0.7);
    return near.length?`You're close to completing <strong>${near.length}</strong> dynamic goal${near.length>1?'s':''}. Finish strong!`:'Log your progress on dynamic habits to see insights here.';
  }
];

function updateInsights(){
  if(!habits.length) return;
  generateInsights();
}

function generateInsights(){
  if(!habits.length) return;
  const list = document.getElementById('insightsList');
  list.innerHTML='';
  const shuffled = [...insightTemplates].sort(()=>Math.random()-0.5).slice(0,3);
  shuffled.forEach((fn,i)=>{
    const card = document.createElement('div');
    card.className='insight-card';
    card.style.animationDelay=(i*0.08)+'s';
    const label = document.createElement('div');
    label.className='insight-quote';
    label.textContent=['Trend','Streak','Tip'][i]||'Note';
    const text = document.createElement('div');
    text.innerHTML=fn(habits);
    card.appendChild(label);
    card.appendChild(text);
    list.appendChild(card);
  });
}

// ==========================
// ===== MODAL EVENTS =======
// ==========================
document.getElementById('modalOverlay').addEventListener('click', e=>{
  if(e.target===this) closeModal();
});
document.addEventListener('keydown', e=>{ if(e.key==='Escape') closeModal(); });

// ==========================
// ===== BOOT ===============
// ==========================
init();
