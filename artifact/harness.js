// Entorno simulado de window.claude para pruebas locales (datos ficticios).
(function(){
  const store = new Map(); const subs = [];
  const snapFor = (path) => { const segs=path.split("/"); if(segs.length%2===1){ const docs=[...store.entries()].filter(([k])=>k.startsWith(path+"/")&&k.split("/").length===segs.length+1).map(([k,v])=>({id:k.split("/").pop(),exists:true,data:()=>JSON.parse(JSON.stringify(v)),metadata:{fromCache:false,hasPendingWrites:false}})); return {docs,size:docs.length,empty:!docs.length,metadata:{fromCache:false,hasPendingWrites:false},docChanges:()=>[]}; }
    const v=store.get(path); return {id:segs.pop(),exists:!!v,data:()=>v?JSON.parse(JSON.stringify(v)):undefined,metadata:{fromCache:false,hasPendingWrites:false}}; };
  const notify = () => setTimeout(()=>subs.forEach(s=>s.cb(snapFor(s.path))),5);
  const docRef = path => ({ id:path.split("/").pop(), path, async set(d){ const js=JSON.stringify(d); if(js.length>262144) throw {code:"invalid_argument",message:"too big "+js.length}; store.set(path, JSON.parse(js)); window.__writes=(window.__writes||0)+1; notify(); }, async get(){ return snapFor(path); }, onSnapshot(cb){ subs.push({path,cb}); setTimeout(()=>cb(snapFor(path)),10); return ()=>{}; }, collection:(p)=>colRef(path+"/"+p) });
  const colRef = path => ({ path, doc:(id)=>docRef(path+"/"+(id||Math.random().toString(36).slice(2))), onSnapshot(cb){ subs.push({path,cb}); setTimeout(()=>cb(snapFor(path)),10); return ()=>{}; } });
  const db = { doc:docRef, collection:colRef }; window.__store=store;
  const SITE="https://ejemploandina.test";
  const pages = {
    [SITE+"/"]: {markdown:"# Constructora Ejemplo Andina S.R.L.\n\nConstrucción de edificios residenciales y condominios en Santa Cruz de la Sierra, Bolivia.\n\n- [Nosotros]("+SITE+"/nosotros)\n- [Proyectos]("+SITE+"/proyectos)\n- [Contacto]("+SITE+"/contacto)\n\nEscríbanos: ventas@ejemploandina.test\n\n![logo](https://img.test/logo@2x.png)", links:[SITE+"/nosotros",SITE+"/proyectos",SITE+"/contacto","https://www.linkedin.com/company/ejemplo-andina-test/","https://wa.me/+59171234567"], metadata:{title:"Constructora Ejemplo Andina | Edificios y condominios en Santa Cruz",description:"Constructora Ejemplo Andina: construcción de edificios residenciales y condominios en Santa Cruz de la Sierra.",url:SITE+"/",statusCode:200}},
    [SITE+"/contacto"]: {markdown:"# Contacto\n\nDirección: Av. Cristo Redentor 1234, Santa Cruz de la Sierra, Bolivia\n\nTeléfono: (3) 3456789\n\n[info@ejemploandina.test](mailto:info@ejemploandina.test)\n\nNIT 1029384756", links:["mailto:info@ejemploandina.test"], metadata:{title:"Contacto",url:SITE+"/contacto",statusCode:200}},
    [SITE+"/nosotros"]: {markdown:"# Nuestro equipo\n\nGerente General: Ing. Mariela Quispe Rojas — correo mquispe@ejemploandina.test\n\nLa empresa fue fundada en 2004.\n\nGerente Técnico: Arq. Pablo Soria Vaca", links:["https://www.linkedin.com/in/mariela-quispe-test/"], metadata:{title:"Nosotros",url:SITE+"/nosotros",statusCode:200}},
    [SITE+"/proyectos"]: {markdown:"# Proyectos\n\n### TORRE HORIZONTE\n\nEdificio de 18 pisos en Equipetrol, Santa Cruz de la Sierra.\n\n### Condominio Las Palmeras\n\nCondominio cerrado en Urubó.", links:[], metadata:{title:"Proyectos",url:SITE+"/proyectos",statusCode:200}}
  };
  const d = n => new Date(Date.now()-n*864e5).toDateString().slice(4);
  window.__calls=[];
  const mcp = {
    async listTools(){ return {servers:[{server:"Firecrawl",authStatus:"connected",tools:[{name:"firecrawl_search",description:""},{name:"firecrawl_scrape",description:""}]}]}; },
    async callTool(server, tool, input){ window.__calls.push([tool,input.query||input.url]); await new Promise(r=>setTimeout(r,20));
      if(tool==="firecrawl_scrape"){ const p=pages[input.url]||pages[input.url+"/"]; if(!p) throw {code:"tool_error",message:"404"}; return {content:[],payload:{...p}}; }
      const q=input.query||""; let web=[],news=[];
      if(/Ejemplo Andina/.test(q)&&/linkedin\.com\/company/.test(q)) web=[{url:"https://bo.linkedin.com/company/ejemplo-andina-test",title:"Constructora Ejemplo Andina | LinkedIn",description:"Constructora Ejemplo Andina. Santa Cruz."}];
      else if(/Ejemplo Andina/.test(q)&&/linkedin\.com\/in/.test(q)) web=[{url:"https://bo.linkedin.com/in/mariela-quispe-test",title:"Mariela Quispe Rojas - Gerente General - Constructora Ejemplo Andina | LinkedIn",description:"Constructora Ejemplo Andina · Santa Cruz"}];
      else if(/Ejemplo Andina/.test(q) && input.sources.includes("news")) news=[{title:"Constructora Ejemplo Andina lanza la Torre Horizonte con inversión de $us 12 millones",url:"https://diarioficticio.test/torre",date:"1 week ago",snippet:"La Constructora Ejemplo Andina inició la preventa de la Torre Horizonte en Santa Cruz."},{title:"Compradores denuncian retraso en condominio de Constructora Ejemplo Andina",url:"https://prensatest.test/reclamo",date:d(60)},{title:"Otra empresa sin relación anuncia resultados",url:"https://diarioficticio.test/otra",date:d(3)}];
      else if(/Ejemplo Andina/.test(q)) web=[{url:SITE+"/",title:"Constructora Ejemplo Andina - Inicio",description:"Constructora en Santa Cruz, Bolivia."}];
      else if(/constructora/i.test(q)&&/Santa Cruz/.test(q)) { web=[{url:SITE+"/",title:"Constructora Ejemplo Andina | Edificios en Santa Cruz",description:"Constructora de edificios en Santa Cruz de la Sierra, Bolivia"},{url:"https://www.facebook.com/x",title:"Las mejores constructoras",description:"Constructora"},{url:"https://cocina.test/",title:"Receta",description:"nada"}]; news=[{title:"Desarrolladora Cumbre Test inicia la Urbanización Valle Alto en Cochabamba",url:"https://diarioficticio.test/valle",date:"3 days ago",snippet:"La Desarrolladora Cumbre Test invertirá en Cochabamba."}]; }
      return {content:[],payload:{success:true,data:{web,news}}}; }
  };
  const sampleFn = async (input, opts) => ({text:"DESARROLLADORA CUMBRE TEST\nAna Lucía Vargas Soliz\nCEO\nana.vargas@cumbre.test\nCel. 71122334\nwww.cumbre.test\nCochabamba - Bolivia"});
  sampleFn.json = async (input) => { window.__sampleCalls=(window.__sampleCalls||0)+1;
    if(input.includes("FUENTES:")) return {fields:[{field:"legal_name",value:"Ejemplo Andina Holding S.A.",source:1,quote:"Ejemplo Andina Holding S.A."},{field:"activity",value:"Construcción de edificios residenciales y condominios",source:1,quote:"Construcción de edificios residenciales y condominios en Santa Cruz de la Sierra"}],executives:[{full_name:"Persona Inventada Rojas",position:"CEO",source:1,quote:"Persona Inventada Rojas, CEO"}],projects:[]};
    if(input.includes("NOMBRADAS")) return [{name:"Desarrolladora Cumbre Test",source:3},{name:"Empresa Fantasma Inventada",source:1}];
    return {email_subject:"x",email_body:"y",linkedin:"z",meeting:"m",pitch:"p"}; };
  sampleFn.limits = async () => ({images:{maxCount:5}});
  window.__saved=[];
  const downloads = { async save(o){ window.__saved.push({filename:o.filename, size:o.data.byteLength}); } };
  const user = { async id(){ return "u_test"; } };
  const caps = {db, mcp, sample: sampleFn, downloads, user};
  window.claude = { use: async (n) => { await new Promise(r=>setTimeout(r,30)); return caps[n]||null; } };
})();
