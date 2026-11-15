const form = document.querySelector('#profile');
const status = document.querySelector('#status');
const keys = ['name', 'email', 'phone', 'city', 'school', 'major', 'degree'];

chrome.storage.local.get(keys).then(saved => {
  for (const key of keys) form.elements[key].value = saved[key] || '';
});

form.addEventListener('submit', async event => {
  event.preventDefault();
  const candidate = Object.fromEntries(keys.map(key => [key, form.elements[key].value.trim()]));
  await chrome.storage.local.set(candidate);
  status.textContent = '正在检查当前页面…';
  try {
    const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
    if (!tab?.id || !/^https:\/\//.test(tab.url || '')) throw new Error('请打开 HTTPS 投递页面后再使用。');
    const [execution] = await chrome.scripting.executeScript({target: {tabId: tab.id}, func: safeFill, args: [candidate]});
    const result = execution.result;
    if (!result.safe) throw new Error(result.message);
    status.textContent = `已填写 ${result.filled} 项，跳过 ${result.skipped} 项。\n请逐项检查；最终提交仍由你本人完成。`;
  } catch (error) {
    status.textContent = error.message || '当前页面无法安全填写。';
  }
});

function safeFill(candidate) {
  const page = (document.body?.innerText || '').slice(0, 100000);
  const injection = /ignore\s+(?:all\s+)?previous|system\s+prompt|忽略(?:以上|之前|所有).*指令|系统提示词|上传.*(?:cookie|密码|密钥)|绕过.*(?:验证|验证码)/i;
  if (injection.test(page)) return {safe: false, message: '页面包含可疑指令，已停止填写。'};
  const aliases = {
    name: ['姓名', '名字', 'name', 'full name'], email: ['邮箱', '电子邮件', 'email', 'e-mail'],
    phone: ['手机', '电话', '联系电话', 'phone', 'mobile'], city: ['城市', '所在地', 'city', 'location'],
    school: ['学校', '院校', 'university', 'school'], major: ['专业', 'major'],
    degree: ['学历', '学位', 'degree', 'education']
  };
  const sensitive = /密码|口令|验证码|动态码|身份证|证件号|银行卡|信用卡|password|passcode|otp|captcha|verification\s*code|national\s*id|bank|credit\s*card/i;
  const controls = [...document.querySelectorAll('input,textarea,select')];
  let filled = 0, skipped = 0;
  for (const control of controls) {
    const type = (control.type || '').toLowerCase();
    const label = [control.labels?.[0]?.innerText, control.getAttribute('aria-label'), control.placeholder, control.name, control.id].filter(Boolean).join(' ');
    if (control.disabled || control.readOnly || ['password','hidden','file','submit','button','checkbox','radio'].includes(type) || sensitive.test(label)) { skipped++; continue; }
    const normalized = label.toLowerCase();
    const key = Object.keys(aliases).find(name => aliases[name].some(alias => normalized.includes(alias.toLowerCase())));
    if (!key || !candidate[key]) { skipped++; continue; }
    if (control.tagName === 'SELECT') {
      const option = [...control.options].find(item => item.text.toLowerCase().includes(candidate[key].toLowerCase()));
      if (!option) { skipped++; continue; }
      control.value = option.value;
    } else control.value = candidate[key];
    control.dispatchEvent(new Event('input', {bubbles: true}));
    control.dispatchEvent(new Event('change', {bubbles: true}));
    control.style.outline = '2px solid #37a56c';
    control.dataset.offerCompassFilled = 'true';
    filled++;
  }
  return {safe: true, filled, skipped};
}
