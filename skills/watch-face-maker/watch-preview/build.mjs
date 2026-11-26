import { readFileSync, writeFileSync } from 'node:fs';
const read = name => readFileSync(new URL(name,import.meta.url),'utf8');
const write = (name,data) => writeFileSync(new URL(name,import.meta.url),data);
write('code.js',[read('../scripts/watch_preview_binding.js'),
  'const repairPanelHTML='+JSON.stringify(read('rebind.html'))+';',
  read('rebind.js'),read('main.js')].join('\n'));
const dep='./node_modules/h264-mp4-encoder/';
const library=read(dep+'embuild/dist/h264-mp4-encoder.web.js').replace(/<\/script/gi,'<\\/script');
const license=read(dep+'LICENSE.md');
write('ui.html',read('panel.html').replace('<!-- encoder -->',()=>'<script>/*\n'+license+'*/\n'+library+'\n</script>'));
