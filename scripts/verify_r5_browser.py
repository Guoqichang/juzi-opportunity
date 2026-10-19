"""Real UI smoke check; writes no user data."""
import json
import os
from playwright.sync_api import sync_playwright

domain=os.environ['POLICY_DOMAIN']
server_ip=os.environ['POLICY_SERVER_IP']

with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,args=[f'--host-resolver-rules=MAP {domain} {server_ip}'])
    page=browser.new_page(viewport={'width':1440,'height':1000},locale='zh-CN')
    errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    response=page.goto(f'https://{domain}/',wait_until='networkidle',timeout=45000)
    page.locator('#priority-actions .action-card').first.wait_for(timeout=30000)
    assert response.status==200
    assert page.locator('#priority-actions .action-card').count()<=5
    assert page.get_by_role('link',name='打开飞书多维表格 ↗').count()==1
    card=page.locator('#priority-actions .action-card').first
    card.locator('summary').click()
    card.locator('input').first.fill('界面检查（未保存）')
    page.wait_for_timeout(16000)
    assert card.locator('input').first.input_value()=='界面检查（未保存）'
    card.locator('input').first.fill('')
    card.locator('summary').click()
    page.screenshot(path='/tmp/juzi-r5-radar.png')
    card.get_by_role('button',name='查看原文与资格').click()
    page.locator('#workspace-assessment').wait_for(state='visible')
    assert page.get_by_text('原文提取草稿 · 待核对',exact=True).count()==1
    page.get_by_role('button',name='定期报告',exact=True).click()
    page.locator('#report-preview').wait_for(state='visible')
    assert not errors,errors
    page.set_viewport_size({'width':390,'height':844})
    page.get_by_role('button',name='机会雷达',exact=True).click()
    page.wait_for_timeout(500)
    dimensions=page.evaluate('({width:innerWidth,document:document.documentElement.scrollWidth,scrollX:scrollX,items:Array.from(document.querySelectorAll("body *")).filter(e=>e.getBoundingClientRect().right+scrollX>innerWidth+1 && e.getBoundingClientRect().width>0).map(e=>({tag:e.tagName,id:e.id,cls:e.className,right:e.getBoundingClientRect().right+scrollX})).slice(0,15)})')
    print(json.dumps(dimensions),flush=True)
    assert dimensions['document'] <= dimensions['width'] + 1
    print(json.dumps({'http_status':response.status,'actions':page.locator('#priority-actions .action-card').count(),'js_errors':errors,'draft_edit_survives_auto_refresh':True,'mobile_no_horizontal_overflow':True}))
    browser.close()
