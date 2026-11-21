const { chromium } = require('playwright');
const path = require('path');
(async () => {
  const b = await chromium.launch(); const ctxB = await b.newContext();
  const V={'xlsx/0.18.5/xlsx.full.min.js':'vend/xlsx-0.18.5/dist/xlsx.full.min.js','pdf.js/3.11.174/pdf.min.js':'vend/pdfjs-dist-3.11.174/build/pdf.min.js','pdf.js/3.11.174/pdf.worker.min.js':'vend/pdfjs-dist-3.11.174/build/pdf.worker.min.js'};
  await ctxB.route('**/cdnjs.cloudflare.com/**', r=>{ const k=Object.keys(V).find(k=>r.request().url().endsWith(k)); return k?r.fulfill({path:V[k],contentType:'application/javascript'}):r.abort(); });
  await ctxB.route('**/fonts.googleapis.com/**', r=>r.abort());
  const p = await ctxB.newPage({ viewport:{width:1400,height:1000} });
  const errs=[]; p.on('pageerror', e=>errs.push('pageerror: '+e.message)); p.on('console', m=>{ if(m.type()==='error') errs.push('console: '+m.text()); });
  const ok=(c,msg)=>{ console.log((c?'✔ ':'✘ ')+msg); if(!c) process.exitCode=1; };
  await p.goto('http://127.0.0.1:8765/test.html#panel'); await p.waitForTimeout(800);
  ok(await p.locator('text=Aún no hay empresas registradas').count()===1, 'estado vacío del panel');
  ok(await p.evaluate(()=>typeof XLSX!=='undefined'), 'SheetJS cargado desde cdnjs');
  // descubrimiento
  await p.goto('http://127.0.0.1:8765/test.html#investigacion'); await p.waitForTimeout(300);
  await p.uncheck('input[name=cities][value="La Paz"]'); await p.uncheck('input[name=cities][value="Cochabamba"]');
  await p.fill('#d-max','1'); await p.click('form[data-form=discover] button.pr');
  await p.waitForFunction(()=>document.querySelector('#job .prog') && !document.querySelector('#job .spin'), null, {timeout:30000});
  const names = await p.evaluate(()=>[...window.__store.entries()].filter(([k])=>k.startsWith('companies/')).map(([k,v])=>v.name));
  console.log('   empresas:', names);
  ok(names.some(n=>/Ejemplo Andina/.test(n)), 'descubre empresa con sitio propio');
  ok(names.includes('Desarrolladora Cumbre Test'), 'descubre empresa nombrada en noticia');
  ok(!names.some(n=>/Fantasma|Receta|mejores/i.test(n)), 'descarta nombres inventados y resultados ajenos');
  const andina = await p.evaluate(()=>[...window.__store.entries()].find(([k,v])=>/Andina/.test(v.name))[1]);
  ok(andina.website==='https://ejemploandina.test' && andina.fields.website.conf==='verificado', 'sitio oficial verificado');
  ok(andina.email==='ventas@ejemploandina.test' && andina.phone, 'correo y teléfono del sitio: '+andina.email+' '+andina.phone);
  ok(andina.execs.some(e=>e.full_name==='Mariela Quispe Rojas'&&e.email==='mquispe@ejemploandina.test'&&e.position==='Gerente General'), 'gerente con correo publicado junto a su nombre');
  ok(!andina.execs.some(e=>/Inventada/.test(e.full_name)), 'Claude: ejecutivo sin cita real descartado');
  ok(!andina.legal_name, 'Claude: razón social sin cita real descartada');
  ok(andina.execs.find(e=>e.full_name==='Pablo Soria Vaca')?.email==null, 'no se atribuye correo al gerente técnico');
  ok(andina.projects.some(x=>x.name==='Torre Horizonte'), 'proyecto desde título en mayúsculas → «Torre Horizonte»');
  ok(andina.news.length===2, 'noticias filtradas por mención de la empresa: '+andina.news.length);
  ok(andina.crm_status==='Investigado' && andina.score>=70, 'estado Investigado y puntuación '+andina.score);
  ok(!JSON.stringify(andina.facts).includes('2x.png'), 'sin correos falsos de imágenes');
  // ficha
  await p.goto('http://127.0.0.1:8765/test.html#empresa-'+andina.id); await p.waitForTimeout(300);
  ok(await p.locator('text=INVESTIGAR MÁS').count()>0, 'ficha muestra INVESTIGAR MÁS');
  await p.screenshot({path:'a_ficha.png', fullPage:true});
  await p.click('button[data-tab=analisis]'); await p.waitForTimeout(150); await p.screenshot({path:'a_analisis.png', fullPage:true});
  // edición manual
  await p.click('button[data-tab=datos]'); await p.locator('details.ed:has(form[data-field=phone]) > summary').first().click();
  await p.locator('form[data-field=phone] input[name=value]').fill('33111222'); await p.locator('form[data-field=phone] button').click(); await p.waitForTimeout(200);
  const ph = await p.evaluate(id=>window.__store.get('companies/'+id).phone, andina.id); ok(ph==='+591 33111222', 'edición manual normaliza y guarda: '+ph);
  // generar contacto
  await p.click('button[data-tab=contacto]'); await p.click('form[data-form=outreach] button.ac'); await p.waitForTimeout(300);
  const drafts = await p.evaluate(id=>window.__store.get('companies/'+id).drafts, andina.id);
  ok(drafts.length===4 && drafts.every(d=>d.status==='borrador'), '4 borradores sin enviar');
  const em=drafts.find(d=>d.channel==='email').body; ok(em.includes('Mariela Quispe Rojas') && em.includes('Torre Horizonte') && !/denuncian/i.test(em), 'correo personalizado, sin mencionar el conflicto');
  ok(/NO mencionar/.test(drafts.find(d=>d.channel==='pitch').body), 'conflicto sólo en el argumento interno');
  await p.screenshot({path:'a_contacto.png', fullPage:true});
  await p.locator('button[data-st=aprobado]').first().click(); await p.waitForTimeout(150);
  await p.locator('button[data-st="enviado manualmente"]').first().click(); await p.waitForTimeout(200);
  ok(await p.evaluate(id=>window.__store.get('companies/'+id).crm_status, andina.id)==='Contactado', 'marcar enviado → CRM Contactado');
  // empresa sin datos no genera mensaje genérico
  const cumbre = await p.evaluate(()=>[...window.__store.entries()].find(([k,v])=>/Cumbre/.test(v.name))[1]);
  // documento Excel
  await p.goto('http://127.0.0.1:8765/test.html#documentos'); await p.waitForTimeout(300);
  const xlsxPath = path.resolve('clientes.xlsx');
  await p.evaluate(()=>{ const ws=XLSX.utils.aoa_to_sheet([["Listado 2026"],[],["Razón social","Ciudad","Correo","Teléfono","Gerente General","Cargo","Observaciones"],["CONSTRUCTORA EJEMPLO ANDINA S.R.L.","Santa Cruz","info@ejemploandina.test","3-3456789","Mariela Quispe Rojas","Gerente General","Cliente desde 2024"],["Inmobiliaria Prueba Norte","Cochabamba","correo-invalido","Pendiente","","",""]]); const wb=XLSX.utils.book_new(); XLSX.utils.book_append_sheet(wb,ws,"Clientes"); window.__x=XLSX.write(wb,{type:'array',bookType:'xlsx'}); });
  const buf = Buffer.from(await p.evaluate(()=>Array.from(new Uint8Array(window.__x)))); require('fs').writeFileSync(xlsxPath, buf);
  await p.setInputFiles('#files', xlsxPath); await p.waitForTimeout(600);
  ok(await p.locator('text=1</b> empresas nuevas').count()===0 && (await p.content()).includes('<b>1</b> empresas nuevas'), 'vista previa: 1 nueva, 1 existente');
  await p.screenshot({path:'a_doc.png', fullPage:true});
  await p.click('button[data-act=import]'); await p.waitForTimeout(500);
  const all = await p.evaluate(()=>[...window.__store.entries()].filter(([k])=>k.startsWith('companies/')).map(([k,v])=>v));
  ok(all.length===3, 'importación sin duplicar: '+all.length+' empresas');
  const norte=all.find(c=>c.name==='Inmobiliaria Prueba Norte'); ok(norte && !norte.email && !norte.phone && norte.city==='Cochabamba', 'datos inválidos/«Pendiente» no importados');
  // foto (OCR simulado)
  require('fs').writeFileSync('tarjeta.png', Buffer.from([137,80,78,71,1,2,3]));
  await p.setInputFiles('#files', path.resolve('tarjeta.png')); await p.waitForTimeout(500);
  ok((await p.content()).includes('Ana Lucía Vargas Soliz'), 'foto: texto transcrito y propuestas');
  await p.selectOption('#rv-co', cumbre.id); await p.waitForTimeout(150); await p.click('button[data-act=apply]'); await p.waitForTimeout(400);
  const c2 = await p.evaluate(id=>window.__store.get('companies/'+id), cumbre.id);
  ok(c2.execs.some(e=>e.full_name==='Ana Lucía Vargas Soliz'&&e.position==='CEO'&&e.email==='ana.vargas@cumbre.test') && c2.website==='https://cumbre.test', 'tarjeta incorporada: ejecutiva, correo y web');
  ok(Object.values(c2.sources).some(s=>s.type==='documento'), 'documento registrado como fuente');
  // PDF con texto
  await p.goto('http://127.0.0.1:8765/test.html#documentos'); await p.waitForTimeout(200);
  await p.setInputFiles('#files', path.resolve('propuesta.pdf')); await p.waitForTimeout(1500);
  const pc = await p.content(); ok(pc.includes('Juan Carlos Perez Rojas') && pc.includes('+591 33998877'), 'PDF: texto leído y propuestas (ejecutivo, teléfono)');
  // exportar
  await p.goto('http://127.0.0.1:8765/test.html#ajustes'); await p.waitForTimeout(200); await p.click('button[data-act=export]'); await p.waitForTimeout(500);
  const saved = await p.evaluate(()=>window.__saved); ok(saved.length===1 && saved[0].filename.endsWith('.xlsx') && saved[0].size>5000, 'Excel exportado: '+JSON.stringify(saved));
  // panel y CRM
  await p.goto('http://127.0.0.1:8765/test.html#panel'); await p.waitForTimeout(300); await p.screenshot({path:'a_panel.png', fullPage:true});
  await p.goto('http://127.0.0.1:8765/test.html#seguimiento'); await p.waitForTimeout(300); await p.screenshot({path:'a_crm.png'});
  // actualización general
  await p.goto('http://127.0.0.1:8765/test.html#cambios'); await p.waitForTimeout(200); await p.click('button[data-act=updateall]');
  await p.waitForFunction(()=>document.querySelector('#job .prog') && !document.querySelector('#job .spin'), null, {timeout:30000});
  ok((await p.textContent('#job')).includes('Actualizando 3 empresas'), 'actualización general terminada');
  console.log('   consultas Firecrawl simuladas:', await p.evaluate(()=>window.__calls.length), '· escrituras:', await p.evaluate(()=>window.__writes));
  // móvil
  const m = await ctxB.newPage({ viewport:{width:390,height:844} }); await m.goto('http://127.0.0.1:8765/test.html#empresa-'+andina.id); await m.waitForTimeout(500);
  ok(await m.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1), 'sin desplazamiento horizontal en móvil'); await m.screenshot({path:'a_mobile.png'});
  console.log('errores:', errs.length?errs:'ninguno'); await b.close();
})();
