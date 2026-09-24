const { chromium } = require('playwright');
const fs=require('fs'),path=require('path'),{spawn}=require('child_process'),{once}=require('events');
const root=path.resolve(__dirname,'..');
const venv=path.join(root,process.platform==='win32'?'.venv/Scripts/python.exe':'.venv/bin/python');
const python=process.env.ATLAS_TEST_PYTHON||(fs.existsSync(venv)?venv:'python');
(async()=>{
 const server=spawn(python,['-u','-m','scripts.serve_smoke'],{cwd:root,stdio:['pipe','pipe','inherit'],env:{...process.env,PYTHONUTF8:'1'}});
 let browser;
 try{
  const port=await Promise.race([new Promise((resolve,reject)=>{server.stdout.once('data',d=>resolve(Number(d.toString().trim())));server.once('exit',c=>reject(Error('Server exited '+c)))}),new Promise((_,reject)=>{const t=setTimeout(()=>reject(Error('Server startup timeout')),10000);t.unref()})]);
  if(!Number.isInteger(port))throw Error('Invalid server port');
  browser=await chromium.launch({headless:true,...(process.env.ATLAS_BROWSER_CHANNEL?{channel:process.env.ATLAS_BROWSER_CHANNEL}:{})});
  const page=await browser.newPage({viewport:{width:1500,height:1100}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(`http://127.0.0.1:${port}`);await page.locator('.demo-banner').waitFor();
  if(!await page.evaluate(()=>window.ATLAS_BRIDGE&&D.papers.every(p=>p.demo)))throw Error('Demo/live bridge not ready');
  fs.mkdirSync(path.join(root,'docs/assets'),{recursive:true});
  await page.screenshot({path:path.join(root,'docs/assets/overview.png'),fullPage:true});
  for(const name of ['我的知识库','知识图谱','文献雷达','研究问题','来源与运行','使用指南']){
   await page.getByRole('button',{name,exact:true}).click();
   if(name==='我的知识库'){
    await page.locator('#wiki-search').fill('DEMO');await page.locator('.page-item').first().click();
    if(!await page.locator('#document .markdown').textContent())throw Error('Empty wiki');
    if(!await page.locator('#document .backlinks [data-page]').count())throw Error('Missing backlinks');
    await page.locator('#document .backlinks [data-page]').first().click();
   }
   if(name==='知识图谱'){
    await page.locator('#graph-canvas canvas').first().waitFor();
    await page.screenshot({path:path.join(root,'docs/assets/graph.png'),fullPage:true});
    const n=await page.evaluate(()=>atlasGraph.nodes().length);
    if(await page.evaluate(()=>atlasGraph.nodes().filter(n=>n.data('type')==='question').some(n=>n.degree()===0)))throw Error('Disconnected demo question');
    await page.getByRole('button',{name:'展开原文陈述',exact:true}).click();
    if(await page.evaluate(()=>atlasGraph.nodes().length)<=n)throw Error('No statement expansion');
    await page.evaluate(()=>atlasGraph.nodes().filter(n=>n.data('type')==='source').first().emit('tap'));
    await page.getByRole('button',{name:'只看所选邻域',exact:true}).click();
    await page.getByRole('button',{name:'全部连接',exact:true}).click();
    await page.locator('#graph-topic').selectOption('Query privacy');
    await page.locator('#graph-layout').selectOption('concentric');
   }
   if(name==='文献雷达'){await page.locator('#q').fill('Audio');if(await page.locator('.paper-row').count()!==1)throw Error('Search failed')}
   if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw Error('Desktop overflow '+name);
  }
  await page.getByRole('button',{name:'我的知识库',exact:true}).click();
  await page.getByRole('button',{name:'添加材料',exact:false}).click();
  await page.locator('#note-title').fill('DEMO browser test note');
  await page.locator('#note-text').fill('Synthetic test note. [[concepts/query-privacy|Query privacy]]');
  await page.locator('#submit-job').click();await page.waitForFunction(()=>!document.querySelector('#modal').open,{},{timeout:15000});
  await page.waitForFunction(()=>document.querySelector('#document')?.textContent.includes('Synthetic test note'),{},{timeout:15000});
  await page.setViewportSize({width:430,height:932});
  for(const name of ['知识概览','我的知识库','知识图谱','文献雷达','研究问题','来源与运行','使用指南']){
   await page.getByRole('button',{name,exact:true}).click();
   if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw Error('Mobile overflow '+name);
  }
  await page.goto(require('url').pathToFileURL(path.join(root,'output/index.html')).href);
  await page.getByRole('button',{name:'向知识库提问',exact:false}).first().click();
  if(!await page.locator('#submit-job').isDisabled())throw Error('Offline mutation enabled');
  if(errors.length)throw Error(errors.join('\n'));
  console.log('PASS: synthetic demo, 7 views, graph, backlinks, search, live note persistence, mobile, offline');
 }finally{if(browser)await browser.close();server.stdin.end();if(server.exitCode===null)await once(server,'exit')}
})().catch(e=>{console.error(e);process.exitCode=1});
