"use strict";
const researchState = {data:null, active:"radar", questions:[], entries:[], report:null, settingsDirty:false, agentEnabled:false};
const identityPolicy = "separate-source-urls-v1";
const researchLabels = {event:"活动 / 路演 / 竞赛",compute_support:"算力与补贴",cloud_credit:"创业云资源",business_support:"企业支持",information:"资讯线索",open_call:"招募 / 申报通知",program:"政策办法",result_notice:"结果公示",recap:"活动回顾",expired_notice:"原文标注失效 / 废止",discovered:"等待抓取正文",collected:"原文已归档",blocked:"采集受阻",done:"完成",partial:"部分完成",running:"进行中"};

function showWorkspace(name){
  researchState.active=name;
  for(const button of document.querySelectorAll("[data-workspace]")){
    button.classList.toggle("active",button.dataset.workspace===name);
    $("workspace-"+button.dataset.workspace).hidden=button.dataset.workspace!==name;
  }
}
function safeResearchLink(text,url){
  if(!/^https?:\/\//i.test(url))return node("span",text);
  const a=node("a",text);a.href=url;a.target="_blank";a.rel="noreferrer";return a;
}
function researchError(error){notice(error.message,true);}
function sourceLabel(url){try{return new URL(url).hostname;}catch{return url;}}
function sourceLine(url){const line=node("p",undefined,"muted");line.append(node("span","来源："),safeResearchLink(sourceLabel(url),url));return line;}
function buttonAction(text,fn,cls){
  const button=node("button",text,cls);button.addEventListener("click",async()=>{
    button.disabled=true;try{await fn();}catch(e){researchError(e);}finally{button.disabled=false;}
  });return button;
}
function renderCandidates(){
  $("candidate-list").replaceChildren();
  const kind=$("candidate-kind").value, scope=$("candidate-scope").value, term=$("candidate-filter").value.trim().toLowerCase();
  const items=(researchState.data?.candidates||[]).filter(c=>(kind==="all"||c.category===kind)&&(scope==="all"||(["open_call","program"].includes(c.document_kind)&&c.deadline_hint_status!=="possibly_past"))&&(!term||(c.title+c.reasons.join(" ")).toLowerCase().includes(term)));
  if(!items.length)$("candidate-list").append(node("p","暂未发现符合筛选条件的线索。可启动发现或调整关注主题。","muted"));
  for(const c of items){
    const card=node("article",undefined,"candidate-card");const tags=node("div",undefined,"tags");
    tags.append(node("span",researchLabels[c.category]||c.category,"badge"),node("span",researchLabels[c.document_kind]||c.document_kind,"badge"));
    if(c.deadline_hint_status==="possibly_past")tags.append(node("span","疑似已截止，需核对","badge wait"));
    card.append(tags,node("h3",c.title),sourceLine(c.url),node("p",c.reasons.join("；")),node("p",`${researchLabels[c.state]} · 首次发现 ${new Date(c.first_seen).toLocaleDateString("zh-CN")}`,"muted"));
    const origin=c.origins[c.origins.length-1];
    card.append(node("p",origin?.type==="search"?"发现方式：搜索“"+origin.query+"”":origin?.type==="agent"?"发现方式：OpenCode 深入研究":"发现方式：官网栏目追踪","muted"));
    if(c.hints.error)card.append(node("p",c.hints.error,"warning"));
    if(c.hints.warnings?.length)card.append(node("p",c.hints.warnings.join("；"),"warning"));
    if(c.hints.alternative_for)card.append(node("p","为“"+c.hints.alternative_for.title+"”找到的替代线索，是否同一事项仍待核对。","muted"));
    if(c.hints.research){
      const r=c.hints.research, names={running:"深入研究中",recovered:"已重新取得原文",alternatives_found:"已找到替代来源",unresolved:"本轮未找到可用原文",budget_exhausted:"今日研究预算已用完"};
      const details=node("details");details.append(node("summary",names[r.state]||r.state));
      if(r.note)details.append(node("p",r.note));
      for(const step of r.steps||[])details.append(node("p",`${{search:"搜索",fetch:"抓取",render:"浏览器读取"}[step.action]||step.action} · ${step.argument} · ${step.state==="done"?"完成":step.error||"未完成"}`,"muted"));
      for(const a of r.alternatives||[]){const line=node("p");line.append(safeResearchLink(a.title,a.url),node("span"," · 与本条的关系待核对","muted"));details.append(line);}
      card.append(details);
    }
    const actions=node("div",undefined,"actions");actions.append(safeResearchLink("打开来源 ↗",c.url));
    if(c.state!=="collected")actions.append(buttonAction(c.state==="blocked"?"重试采集":"采集正文",async()=>{await api(`/api/research/${state.company}/candidates/${c.id}/collect`,"POST",{});notice("已加入正文与附件采集队列。");await loadRadar();}));
    if(c.source_id)actions.append(buttonAction("查看证据与缺口",async()=>{await refresh();await select(c.source_id);showWorkspace("assessment");}));
    if(researchState.agentEnabled)actions.append(buttonAction("深入查找原文",async()=>{await api(`/api/research/${state.company}/candidates/${c.id}/investigate`,"POST",{});notice("已加入深入研究队列。将搜索或用浏览器读取公开来源；刷新可查看步骤和结果。");await loadRadar();}));
    card.append(actions,followupEditor(c.id,researchState.data.feedback?.[c.id]));$("candidate-list").append(card);
  }
}
async function loadRadar(){
  const status=await api("/api/status");
  researchState.agentEnabled=!!status.research_agent?.configured;
  $("worker-status").textContent=status.worker?.running?"自动任务运行中":"自动任务未运行";
  $("worker-status").className="muted "+(status.worker?.running?"status-good":"status-bad");
  $("discover-now").disabled=state.company==="demo";
  if(state.company==="demo"){
    $("action-panel").hidden=true;researchState.data=null;$("discovery-summary").textContent="演示企业只用于验证评估规则。切换句子互动查看真实自动发现与报告。";renderCandidates();return;
  }
  $("action-panel").hidden=false;
  const data=await api("/api/research/"+state.company);researchState.data=data;
  const latest=data.runs[0], settings=data.config.settings;
  const parts=[`当前积累 ${data.candidates.length} 条线索 · 每 ${settings.interval_hours} 小时发现一次 · ${settings.timezone} ${settings.report_hour}:00 生成报告`];
  if(researchState.agentEnabled)parts.push(`OpenCode 深入研究已接入 · 今日剩余 ${status.research_agent.remaining} / ${status.research_agent.daily_limit} 次 · 每次最多 6 步`);
  if(latest){const s=latest.summary;parts.push(`最近一轮：${researchLabels[latest.state]||latest.state} · 新发现 ${s.new_candidates??"…"} 条 · 归档 ${s.collected??"…"} 个来源 · 失败项 ${s.errors?.length??0}`);}
  else parts.push("尚未运行主动发现。");
  $("discovery-summary").textContent=parts.join("\n");
  $("research-topics").value=settings.topics.join("，");$("research-exclusions").value=settings.exclude_terms.join("，");
  $("research-interval").value=String(settings.interval_hours);$("report-mode").value=settings.report_mode;
  $("research-queries").value=settings.queries.join("\n");
  $("research-runs").replaceChildren();
  for(const run of data.runs.slice(0,8)){
    const item=node("div",undefined,"run-item");item.append(node("strong",new Date(run.started_at).toLocaleString("zh-CN")+" · "+(researchLabels[run.state]||run.state)));
    item.append(node("p",`搜索 ${run.summary.queries?.length??0} 组 · 栏目 ${run.summary.hubs?.length??0} 页 · 正文归档 ${run.summary.collected??0} 个`,"muted"));
    for(const e of run.summary.errors||[])item.append(node("pre",e.stage+" · "+e.url+"\n"+e.error));
    $("research-runs").append(item);
  }
  renderCandidates();
  await loadActions();
}
async function loadReports(){
  $("report-now").disabled=state.company==="demo";
  const reports=await api("/api/reports/"+state.company);$("report-list").replaceChildren();
  function show(report){researchState.report=report;renderReport(report);$("report-download").hidden=false;$("report-download").href=`/api/reports/${state.company}/${report.id}/download`;}
  for(const report of reports){$("report-list").append(buttonAction(new Date(report.created_at).toLocaleString("zh-CN")+" · "+({daily:"日报",weekly:"周报",manual:"即时报告"}[report.kind]),()=>show(report)));}
  if(reports.length)show(reports[0]);else{$("report-preview").textContent="还没有报告。自动任务会按设定的节奏生成；也可以立即生成当前报告。";$("report-download").hidden=true;}
}
function renderReport(report){
  const box=$("report-preview"),p=report.payload;box.replaceChildren();
  box.append(node("h2",{daily:"每日简报",weekly:"每周汇总",manual:"即时报告"}[report.kind]),node("p",new Date(report.created_at).toLocaleString("zh-CN"),"muted"));
  box.append(node("p",`新增 ${p.new_candidates.length} 条线索 · 已复核可推进 ${p.actionable.length} 条 · 待核实 ${p.needs_review.length} 条`,"summary-strip"));
  if(p.identity_policy!==identityPolicy)box.append(node("p","这份历史报告使用旧版标题去重，优先清单可能遗漏不同批次。请生成当前报告查看更新结果。","warning"));
  else box.append(node("p",`不同来源分别保留，标题相似不代表同一事项。优先展示 ${p.priority.length} / ${p.priority_total} 条线索，其余可在机会雷达查看。`,"muted"));
  function section(title,rows,render,empty){box.append(node("h3",title));if(!rows.length){box.append(node("p",empty,"muted"));return;}const list=node("ul");for(const row of rows){const item=node("li");render(item,row);list.append(item);}box.append(list);}
  section("本期优先行动",p.actions||[],(item,a)=>item.append(safeResearchLink(a.title,a.url),node("p",a.next_action+" · 负责人："+(a.followup.owner||"待认领"))),"历史报告尚未生成行动项，请生成当前报告。");
  section("建议优先核对",p.priority||[],(item,c)=>{item.append(safeResearchLink(c.title,c.url),sourceLine(c.url),node("p",`${researchLabels[c.category]} · ${researchLabels[c.state]} · 核实当前批次、时间与适用主体`,"muted"));},"暂无足够明确的招募或申报通知。");
  section("已复核可推进",p.actionable,(item,c)=>item.append(safeResearchLink(c.title,c.source_url)),"尚无完成全部资格复核的项目；线索相关不代表可以申报。");
  section("重要变化",p.changes,(item,c)=>item.append(safeResearchLink(c.title,c.source_url),node("p",c.decision)),"暂无与上期相比的已记录变化。");
  section("未来七天疑似截止（待核实）",p.deadline_hints,(item,c)=>item.append(safeResearchLink(c.title,c.url),node("p",c.date+" · "+c.quote)),"未找到明确的七天内截止线索；无日期项目仍须核对原文。");
  section("需要补充的公司资料",p.questions,(item,c)=>item.append(node("span",c.question)),"暂无新增资料缺口。");
  const details=node("details");details.append(node("summary",`本期全部新线索（${p.new_candidates.length}）`));
  for(const c of p.new_candidates){const line=node("p");line.append(safeResearchLink(c.title,c.url),node("span",` · ${researchLabels[c.document_kind]} · ${researchLabels[c.state]}`,"muted"));details.append(line);}box.append(details);
  box.append(node("h3","采集覆盖"),node("p",`发现任务 ${p.coverage.runs} 次 · 搜索 ${p.coverage.queries} 组 · 栏目 ${p.coverage.hubs} 页 · 失败项 ${p.coverage.errors} 个。`));
  const failures=node("details");failures.append(node("summary","查看采集缺口"));for(const run of p.runs)for(const e of run.summary.errors||[])failures.append(node("p",e.url+"\n"+e.error));box.append(failures,node("p","报告基于已记录的采集结果，不代表全网覆盖。公司事实版本 "+p.facts_version,"muted"));
}
function renderAnswer(question,response){
  const card=node("article",undefined,"message");card.append(node("p",question,"question"),node("p",response.answer));
  if(response.identity_policy!==identityPolicy)card.append(node("p","历史回答可能省略了文字相同但来源不同的引用，可重新提问获取完整来源。","muted"));
  for(const citation of response.citations){
    const item=node("div",undefined,"citation");item.append(node("strong",citation.title),node("p",citation.quote));
    item.append(safeResearchLink(citation.source_ref,citation.source_ref),node("p",`${citation.citation} · ${citation.status}`,"muted"));card.append(item);
  }
  card.append(node("p",`证据检索 · 公司事实版本 ${response.facts_version} · ${new Date(response.as_of).toLocaleString("zh-CN")}`,"muted"));
  $("conversation").append(card);$("conversation").scrollTop=$("conversation").scrollHeight;
}
async function loadQuestions(){
  const questions=await api("/api/questions/"+state.company);researchState.questions=questions;$("followup-list").replaceChildren();
  for(const question of questions){
    const details=node("details",undefined,"followup");details.append(node("summary",question.label),node("p",question.question));
    const form=node("form");const valueLabel=node("label","填写答案");let input;
    if(question.type==="boolean"){
      input=node("select");for(const [value,text] of [["","请选择"],["true","是"],["false","否"]]){const option=node("option",text);option.value=value;input.append(option);}
    }else{input=node("input");input.type=question.type==="number"?"number":"text";if(input.type==="number")input.step="any";}
    input.required=true;valueLabel.append(input);
    const statusLabel=node("label","确认程度");const status=node("select");for(const [value,text] of [["estimate","尚待核实 / 估算"],["confirmed","已确认，有依据"]]){const option=node("option",text);option.value=value;status.append(option);}statusLabel.append(status);
    const refLabel=node("label","依据或核验说明");const ref=node("input");ref.placeholder="资料名称、记录链接或核验说明";refLabel.append(ref);
    const validity=node("label","有效截止日（如适用）");const end=node("input");end.type="date";validity.append(end);
    const submit=node("button","保存答案并更新评估","primary");form.append(valueLabel,statusLabel,refLabel,validity,submit);
    form.addEventListener("submit",async e=>{e.preventDefault();submit.disabled=true;try{
      const value=question.type==="boolean"?input.value==="true":question.type==="number"?Number(input.value):input.value;
      await api("/api/answers/"+state.company,"POST",{field:question.field,expected_version:question.facts_version,fact:{value,status:status.value,evidence:ref.value,valid_to:end.value||null}});
      await loadQuestions();await loadProfile();await refresh();notice("答案已保存。后续评估和预填会使用新的公司事实版本。");
    }catch(error){researchError(error);}finally{submit.disabled=false;}});
    details.append(form);$("followup-list").append(details);
  }
  if(!questions.length)$("followup-list").append(node("p","当前已建模条件没有待回答的公司事实问题。","muted"));
}
async function loadKnowledge(){
  const entries=await api("/api/knowledge/"+state.company);researchState.entries=entries;$("knowledge-list").replaceChildren();
  for(const entry of entries){const item=node("div",undefined,"knowledge-item");item.append(node("strong",entry.title),node("p",`版本 ${entry.version} · ${entry.status==="verified"?"内容已核对":"参考资料"} · ${entry.source_ref}`));item.append(buttonAction("查看 / 更新",()=>{
    $("knowledge-key").value=entry.key;$("knowledge-version").value=entry.version;$("knowledge-title").value=entry.title;$("knowledge-ref").value=entry.source_ref;$("knowledge-body").value=entry.body;$("knowledge-status").value=entry.status;$("knowledge-editor").open=true;
  }));$("knowledge-list").append(item);}
  $("conversation").replaceChildren();for(const turn of await api("/api/qa/"+state.company))renderAnswer(turn.question,turn.response);
  await loadQuestions();
}
async function loadForms(){
  const sources=await api("/api/sources");$("form-source").replaceChildren();
  for(const source of sources.filter(s=>(s.kind==="demo")===(state.company==="demo"))){const option=node("option",source.title);option.value=source.id;$("form-source").append(option);}
  const drafts=await api("/api/forms/"+state.company);$("form-drafts").replaceChildren();
  for(const draft of drafts){
    const card=node("div",undefined,"draft-card");card.append(node("h3",draft.payload.title),node("p",draft.payload.template_name+` · 公司事实版本 ${draft.facts_version}`));
    if(draft.stale)card.append(node("p","资料或政策已改变，请重新生成。","warning"));
    else card.append(node("p",draft.payload.missing.length?"还需填写："+draft.payload.missing.join("、"):"通用字段已齐全，请映射并核对官方申报表。","muted"));
    if(draft.payload.missing.length)card.append(buttonAction("通过问答补齐资料",async()=>{showWorkspace("knowledge");await loadKnowledge();}));
    const table=node("table",undefined,"draft-table");const head=node("tr");for(const label of ["字段","草稿值","依据"])head.append(node("th",label));table.append(head);
    for(const field of draft.payload.fields){const row=node("tr");for(const text of [field.label,field.value===null?"待补充":String(field.value),field.evidence||"—"])row.append(node("td",text));table.append(row);}card.append(table,node("p",draft.payload.note,"muted"));
    if(!draft.stale){const link=node("a","下载准备草稿","button");link.href=`/api/forms/${state.company}/${draft.id}/download`;card.append(link);}$("form-drafts").append(card);
  }
}
for(const b of document.querySelectorAll("[data-workspace]"))b.addEventListener("click",async()=>{showWorkspace(b.dataset.workspace);try{await ({radar:loadRadar,reports:loadReports,knowledge:loadKnowledge,forms:loadForms,assessment:refresh}[b.dataset.workspace])();}catch(e){researchError(e);}});
$("profile-toggle").addEventListener("click",()=>showWorkspace("assessment"));
$("candidate-kind").addEventListener("change",renderCandidates);$("candidate-scope").addEventListener("change",renderCandidates);$("candidate-filter").addEventListener("input",renderCandidates);
act("discover-now",async()=>{await api("/api/research/discover","POST",{company_id:state.company});notice("已启动主动发现。搜索、栏目扫描和正文采集由自动任务执行，运行记录会持续保留。");await loadRadar();});
act("save-research",async()=>{if(!researchState.data)throw new Error("请切换到句子互动。");const config=researchState.data.config;const split=value=>value.split(/[,，\n]/).map(s=>s.trim()).filter(Boolean);const settings={...config.settings,topics:split($("research-topics").value),exclude_terms:split($("research-exclusions").value),queries:$("research-queries").value.split("\n").map(s=>s.trim()).filter(Boolean),interval_hours:Number($("research-interval").value),report_mode:$("report-mode").value};await api(`/api/research/${state.company}/config`,"PUT",{settings,expected_version:config.version});researchState.settingsDirty=false;await loadRadar();notice("发现范围与报告节奏已保存。");});
act("report-now",async()=>{await api("/api/reports/generate","POST",{company_id:state.company,kind:"manual"});await loadReports();notice("当前报告已生成并保存。");});
$("question-form").addEventListener("submit",async e=>{e.preventDefault();const button=e.currentTarget.querySelector("button");button.disabled=true;const question=$("question-text").value;try{const response=await api("/api/qa/"+state.company,"POST",{question});renderAnswer(question,response);$("question-text").value="";await loadQuestions();}catch(error){researchError(error);}finally{button.disabled=false;}});
for(const chip of document.querySelectorAll("[data-question]"))chip.addEventListener("click",()=>{$("question-text").value=chip.dataset.question;$("question-form").requestSubmit();});
$("knowledge-form").addEventListener("submit",async e=>{e.preventDefault();const button=e.currentTarget.querySelector("button");button.disabled=true;try{await api("/api/knowledge/"+state.company,"POST",{key:$("knowledge-key").value||crypto.randomUUID(),expected_version:Number($("knowledge-version").value),title:$("knowledge-title").value,source_ref:$("knowledge-ref").value,body:$("knowledge-body").value,status:$("knowledge-status").value});e.target.reset();$("knowledge-key").value="";$("knowledge-version").value="0";await loadKnowledge();notice("资料已保存为新的知识版本。");}catch(error){researchError(error);}finally{button.disabled=false;}});
$("prepare-form").addEventListener("submit",async e=>{e.preventDefault();const button=e.currentTarget.querySelector("button");button.disabled=true;try{await api("/api/forms/"+state.company,"POST",{source_id:$("form-source").value});await loadForms();notice("已生成准备草稿，未执行外部填表或提交。");}catch(error){researchError(error);}finally{button.disabled=false;}});
$("company").addEventListener("change",()=>{setTimeout(()=>{loadRadar().catch(researchError);if(researchState.active==="knowledge")loadKnowledge().catch(researchError);if(researchState.active==="reports")loadReports().catch(researchError);if(researchState.active==="forms")loadForms().catch(researchError);},0);});
for(const id of ["research-topics","research-exclusions","research-interval","report-mode","research-queries"])$(id).addEventListener("input",()=>{researchState.settingsDirty=true;});
loadRadar().catch(researchError);
setInterval(()=>{if(researchState.active==="radar"&&!document.querySelector(".manual-editor[data-dirty=true]")&&!researchState.settingsDirty&&!$("research-topics").matches(":focus")&&!$("research-queries").matches(":focus"))loadRadar().catch(()=>{});},15000);


function fieldControl(form,label,value,options){
  const wrap=node("label",label),input=node(options?"select":"input");
  if(options)for(const option of options)input.append(Object.assign(node("option",option),{value:option}));
  input.value=value||"";wrap.append(input);form.append(wrap);return input;
}
function followupEditor(id,data={}){
  const details=node("details"),form=node("form",undefined,"manual-editor");
  details.append(node("summary",`跟进：${data.status||"待核实"} · ${data.owner||"待认领"}`));
  const status=fieldControl(form,"跟进状态",data.status||"待核实",["待核实","值得跟进","不适合","已处理"]);
  const owner=fieldControl(form,"负责人",data.owner||"");owner.maxLength=200;
  const note=fieldControl(form,"备注",data.note||"");note.maxLength=4000;
  form.addEventListener("input",()=>{form.dataset.dirty="true";});
  const save=node("button","保存跟进");save.type="submit";form.append(save);
  form.addEventListener("submit",async e=>{e.preventDefault();save.disabled=true;try{
    await api(`/api/feedback/${state.company}/${id}`,"PUT",{status:status.value,owner:owner.value,note:note.value,expected_version:data.version||0});
    form.dataset.dirty="false";notice("跟进已保存，将在下一轮同步到飞书。");await loadRadar();
  }catch(error){researchError(error);}finally{save.disabled=false;}});
  details.append(form);return details;
}
async function loadActions(){
  const [data,sync]=await Promise.all([api("/api/actions/"+state.company),api("/api/base-sync")]);
  $("priority-actions").replaceChildren();
  for(const a of data.actions){
    const card=node("article",undefined,"candidate-card action-card");
    card.append(node("span",a.decision,"badge"),node("h3",a.title),node("p",a.next_action,"next-action"));
    if(a.deadline)card.append(node("p","截止时间（待核对）："+a.deadline,"muted"));
    card.append(sourceLine(a.url));
    if(a.source_id)card.append(buttonAction("查看原文与资格",async()=>{await refresh();await select(a.source_id);showWorkspace("assessment");}));
    card.append(followupEditor(a.candidate_id,a.followup));$("priority-actions").append(card);
  }
  if(!data.actions.length)$("priority-actions").append(node("p","暂无需要优先跟进的行动，待新机会或公司资料更新。","muted"));
  $("base-status").replaceChildren();$("sync-attention").replaceChildren();
  if(sync.enabled){
    $("base-status").append(safeResearchLink("打开飞书多维表格 ↗",sync.url),node("p",sync.finished_at?"每 5 分钟同步 · 最近 "+new Date(sync.finished_at).toLocaleTimeString("zh-CN"):"正在进行首次同步","muted"));
    if(sync.conflicts?.length||sync.errors?.length){
      $("sync-attention").append(node("p","有同步内容需要处理；未覆盖两边的修改。","warning"));
      for(const conflict of sync.conflicts||[]){
        const box=node("details");box.append(node("summary",conflict.title||conflict.key));
        box.append(node("pre","工作台：\n"+JSON.stringify(conflict.local,null,2)+"\n飞书：\n"+JSON.stringify(conflict.base,null,2)));
        for(const [choice,label] of [["local","采用工作台内容"],["base","采用飞书内容"]])box.append(buttonAction(label,async()=>{await api("/api/base-sync/resolve","POST",{table:conflict.table,key:conflict.key,choice,expected_hash:conflict.hash});notice("已记录处理选择，下一轮同步生效。");}));
        $("sync-attention").append(box);
      }
      for(const error of sync.errors||[])$("sync-attention").append(node("p",(error.key?error.key+"：":"")+error.error,"warning"));
    }
  }
  $("source-relations").replaceChildren();
  for(const r of data.relations){
    const box=node("details");box.append(node("summary",r.left_title+" ↔ "+r.right_title));
    const form=node("form",undefined,"manual-editor");
    const relation=fieldControl(form,"关系判断",r.relation,["待核对","同批次转载","同批次解读","补充更正","不同事项"]),program=fieldControl(form,"项目名称",r.program),batch=fieldControl(form,"批次",r.batch),evidence=fieldControl(form,"判断依据",r.evidence);
    form.addEventListener("input",()=>{form.dataset.dirty="true";});const save=node("button","保存关系");save.type="submit";form.append(save);
    form.addEventListener("submit",async e=>{e.preventDefault();save.disabled=true;try{await api("/api/relations/"+state.company,"POST",{left_id:r.left_id,right_id:r.right_id,relation:relation.value,program:program.value,batch:batch.value,evidence:evidence.value,expected_version:r.version});form.dataset.dirty="false";await loadActions();notice("来源关系已记录，两份原文分别保留。");}catch(error){researchError(error);}finally{save.disabled=false;}});
    box.append(form);$("source-relations").append(box);
  }
  if(!data.relations.length)$("source-relations").append(node("p","暂未产生待核对的来源关系。","muted"));
}
