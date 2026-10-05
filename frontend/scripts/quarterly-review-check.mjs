// Opt-in, read-only check against the existing ESDS Q1 FY2027 corpus.
import assert from 'node:assert/strict';
import { chromium } from 'playwright';
const browser=await chromium.launch({headless:true});
const page=await browser.newPage({viewport:{width:1440,height:1000}});
const errors=[];page.on('pageerror',e=>errors.push(e.message));
try {
 await page.goto(process.env.ALMONDS_BASE_URL || 'http://127.0.0.1:5173/',{waitUntil:'networkidle'});
 const area=page.locator('#financials');
 await area.getByRole('combobox',{name:'Company',exact:true}).selectOption('ESDS');
 const review=area.locator('.quarterly-review');await review.waitFor();
 for(const name of ['Quarterly Investor View','Key Developments','Management Signals','What Changed','Management Execution','Valuation Implication','Key Risks','Watch Next Quarter','Investor Conclusion'])
  assert.equal(await review.getByRole('heading',{name,exact:true}).count(),1,name);
 const full=review.locator('details').filter({has:page.locator('summary').filter({hasText:'View full commentary'})});
 assert.equal(await full.getAttribute('open'),null);
 await review.screenshot({path:'/tmp/almonds-phase72-review-desktop.png'});
 await full.locator('summary').click();
 assert.equal(await full.locator('article').count(),87);
 assert.ok(await full.locator('a[href*="#page="]').count());
 await full.locator('summary').click();
 await page.setViewportSize({width:390,height:844});
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),'mobile overflow');
 await review.screenshot({path:'/tmp/almonds-phase72-review-mobile.png'});
 assert.deepEqual(errors,[]);
 console.log('PASS: nine review sections, collapsed 87-statement drill-down, source-page links, desktop/mobile layout, no browser errors');
}finally{await browser.close()}
