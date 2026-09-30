/* components.js —— Web Components 组件层（light DOM，零依赖零构建）
   ------------------------------------------------------------
   职责：把「结构模板（index.html <template>）+ 行为」封装成自定义元素，
   渲染层只创建元素、填数据、订阅事件，不再拼长 HTML 字符串。

   设计约定：
   - light DOM（不用 Shadow DOM）：样式继续走 app.css 全局令牌，不复制样式；
     组件 = 模板克隆 + 属性/方法 + 事件，不改变现有视觉契约。
   - 组件均暴露简单数据方法（setData/setXxx），内部统一处理 DOM 更新。
   - 允许「先 setData 后 append」：未挂载时调用会被暂存，connectedCallback
     后自动重放（模板就绪再填数据）。
   - 事件一律冒泡，外部用 addEventListener 订阅即可（不依赖全局函数名）。
   */

"use strict";

import { esc, toast, fmtTime, shortPath, api, state } from "./core.js";
import { highlightDiff } from "./render.js";
import { markDirty } from "./settings.js";

/* 模板克隆 + 延迟回调重放 */
function mountTpl(el, tplId) {
  if (el.__mounted) return;
  el.__mounted = true;
  const tpl = document.getElementById(tplId);
  if (tpl) el.appendChild(tpl.content.cloneNode(true));
}
/* 挂载完成（字段初始化后）再重放暂存调用：必须在 connectedCallback 末尾调用 */
function finishMount(el) {
  if (el.__pending) {
    const fns = el.__pending;
    el.__pending = null;
    fns.forEach(fn => fn());
  }
}
/* 未挂载时暂存调用，挂载后重放；挂载后直接执行 */
function deferUntilMounted(el, fn) {
  if (el.__mounted) { fn(); return; }
  (el.__pending = el.__pending || []).push(fn);
}

/* ---------------- <spark-msg> 消息（user / assistant / error） ---------------- */
class SparkMsg extends HTMLElement {
  connectedCallback() {
    mountTpl(this, this.getAttribute("type") === "user"
      ? "tpl-msg-user"
      : this.getAttribute("type") === "error" ? "tpl-msg-error" : "tpl-msg-assistant");
    this.msgEl = this.firstElementChild;
    this.textEl = this.msgEl.querySelector(".text");
    // 取消息文本：assistant 用 .text，user 直接读 msgEl（其下只有文本节点与操作栏）
    const textOf = () => this.textEl ? this.textEl.textContent : this.msgEl.textContent;
    const cp = this.msgEl.querySelector(".copy");
    if (cp) {
      cp.onclick = () => {
        navigator.clipboard.writeText(textOf())
          .then(() => toast("已复制"), () => toast("复制失败"));
      };
    }
    const del = this.msgEl.querySelector(".delmsg");
    if (del) {
      del.onclick = async () => {
        // 持久删除（后端同步），刷新后不会复活
        const mid = this.getAttribute("data-mid");
        if (mid) {
          try {
            const sid = this.closest("[data-sid]")?.getAttribute("data-sid") || state.sid;
            const r = await api(`/api/sessions/${sid}/messages/${mid}`, { method: "DELETE" });
            if (!r.ok) { toast("删除失败"); return; }
          } catch (err) { toast("删除失败"); return; }
        }
        this.remove();
        this.dispatchEvent(new CustomEvent("spark:msg-removed", { bubbles: true }));
      };
    }
    const edit = this.msgEl.querySelector(".editmsg");
    if (edit) {
      edit.onclick = () => {
        const mid = this.getAttribute("data-mid");
        if (!mid) { toast("该消息暂不支持编辑"); return; }
        this.dispatchEvent(new CustomEvent("spark:msg-edit", {
          bubbles: true, detail: { mid, text: textOf() },
        }));
      };
    }
    this.dispatchEvent(new CustomEvent("spark:msg-ready", { bubbles: true }));
    finishMount(this);
  }
  setText(t) { deferUntilMounted(this, () => { this.textEl.textContent = t; }); }
  get text() { return this.textEl ? this.textEl.textContent : ""; }
}

/* ---------------- <spark-tool-card> 工具执行卡 ---------------- */
class SparkToolCard extends HTMLElement {
  connectedCallback() {
    mountTpl(this, "tpl-tool-card");
    this.card = this.firstElementChild;
    this.tname = this.querySelector(".tname");
    this.tsum = this.querySelector(".tsum");
    this.tdur = this.querySelector(".tdur");
    this.status = this.querySelector(".status");
    this.out = this.querySelector(".out");
    this.card.querySelector(".thead").onclick = () => this.card.classList.toggle("open");
    finishMount(this);
  }
  setTool(name, summary) {
    deferUntilMounted(this, () => {
      this.tname.textContent = name;
      this.tsum.textContent = summary || "";
    });
  }
  setDuration(ms) {
    deferUntilMounted(this, () => { this.tdur.textContent = ms ? (ms / 1000).toFixed(1) + "s" : ""; });
  }
  setResult(ok, output) {
    deferUntilMounted(this, () => {
      this.status.textContent = ok ? "✓" : "✗";
      this.status.style.color = ok ? "var(--green)" : "var(--red)";
      this.out.classList.remove("diff");
      this.out.textContent = output || "";
    });
  }
  setDiff(text) {
    deferUntilMounted(this, () => {
      this.out.classList.add("diff");
      this.out.innerHTML = highlightDiff(text);
    });
  }
  markInjected(on) {
    deferUntilMounted(this, () => {
      let w = this.card.querySelector(".injwarn");
      if (on) {
        if (!w) {
          w = document.createElement("div");
          w.className = "injwarn";
          w.innerHTML = '<span class="injicon">⚠</span><div><b>已拦截注入指令</b><p>工具返回内容疑似包含恶意指令，已按普通文本忽略</p></div>';
          this.card.appendChild(w);
        }
      } else if (w) {
        w.remove();
      }
    });
  }
  open() { deferUntilMounted(this, () => this.card.classList.add("open")); }
}

/* ---------------- <spark-plan-card> 计划卡 ---------------- */
class SparkPlanCard extends HTMLElement {
  connectedCallback() {
    mountTpl(this, "tpl-plan-card");
    this.card = this.firstElementChild;
    this.ol = this.querySelector("ol");
    finishMount(this);
  }
  setSteps(steps) {
    deferUntilMounted(this, () => {
      this.ol.innerHTML = steps.map(s => "<li>" + esc(s) + "</li>").join("");
    });
  }
  markDone() {
    deferUntilMounted(this, () => {
      this.card.classList.add("done");
      this.ol.querySelectorAll("li").forEach(li => li.classList.add("done"));
    });
  }
}

/* ---------------- <spark-think> 思考块 ---------------- */
class SparkThink extends HTMLElement {
  connectedCallback() {
    mountTpl(this, "tpl-think");
    this.pre = this.querySelector("pre");
    finishMount(this);
  }
  appendText(t) { deferUntilMounted(this, () => { this.pre.textContent += t; }); }
}

/* ---------------- <spark-session-card> 会话列表项 ---------------- */
class SparkSessionCard extends HTMLElement {
  connectedCallback() {
    mountTpl(this, "tpl-session-card");
    this.card = this.firstElementChild;
    this.t = this.querySelector(".t");
    this.s = this.querySelector(".s");
    this.w = this.querySelector(".w");
    this.srow = this.querySelector(".srow");
    finishMount(this);
  }
  setData(d) {
    deferUntilMounted(this, () => {
      this.t.textContent = d.title || "(未命名)";
      this.s.textContent = d.sub || "";
      this.w.textContent = d.workdir ? shortPath(d.workdir) : "";
      this.card.classList.toggle("active", !!d.active);
      if (d.waiting) {
        const r = document.createElement("span"); r.className = "rind"; r.textContent = "审批中";
        this.t.appendChild(r);
      } else if (d.running) {
        const r = document.createElement("span"); r.className = "rind"; r.textContent = "运行中";
        this.t.appendChild(r);
      }
      this.srow.innerHTML = "";
      (d.actions || []).forEach(a => {
        const b = document.createElement("button");
        b.className = "mini" + (a.kind === "stop" || a.kind === "del" ? " " + a.kind : "");
        b.textContent = a.label;
        b.onclick = e => { e.stopPropagation(); a.fn(); };
        this.srow.appendChild(b);
      });
      if (d.onSelect) this.t.onclick = d.onSelect;
    });
  }
}

/* ---------------- <spark-mcp-row> MCP 服务器行 ---------------- */
class SparkMcpRow extends HTMLElement {
  connectedCallback() {
    mountTpl(this, "tpl-mcp-row");
    this.card = this.firstElementChild;
    this.stdioLines = this.querySelector('[data-k="stdioLines"]');
    this.httpLines = this.querySelector('[data-k="httpLines"]');
    this.desc = this.querySelector(".mcpdesc");
    this.inputs = this.querySelectorAll("input[data-k], select[data-k]");
    finishMount(this);
  }
  /* server: 服务器对象（直接引用，输入双向写回）；idx: 序号（删除用） */
  setServer(server, idx) {
    deferUntilMounted(this, () => {
      const s = server;
      s.transport = s.transport || "stdio";
      const http = s.transport === "http";
      this.card.classList.toggle("http", http);
      this.querySelector('[data-k="name"]').value = s.name || "";
      this.querySelector('[data-k="transport"]').value = s.transport;
      this.querySelector('[data-k="command"]').value = s.command || "";
      this.querySelector('[data-k="args"]').value = (s.args || []).join(" ");
      this.querySelector('[data-k="url"]').value = s.url || "";
      this.querySelector('[data-k="headers"]').value = s.headersJson || "";
      this.stdioLines.style.display = http ? "none" : "";
      this.httpLines.style.display = http ? "" : "none";
      this.desc.textContent = http ? "Streamable HTTP · 2026 无状态协议" : "本地子进程 · stdio";
      const self = this;
      this.inputs.forEach(inp => {
        const apply = () => {
          const k = inp.dataset.k;
          if (k === "args") s.args = inp.value.trim() ? inp.value.trim().split(/\s+/) : [];
          else if (k === "headers") s.headersJson = inp.value;
          else s[k] = inp.value.trim();
          markDirty(true);
        };
        if (inp.tagName === "SELECT") {
          inp.onchange = () => {
            s.transport = inp.value;
            self.setServer(s, idx); // 只重渲染本行（局部更新）
            const fk = s.transport === "http" ? "url" : "command";
            self.querySelector('[data-k="' + fk + '"]').focus();
            markDirty(true);
          };
        } else {
          inp.oninput = apply;
        }
      });
      this.querySelector(".del").onclick = () => {
        this.dispatchEvent(new CustomEvent("spark-mcp:remove", { bubbles: true, detail: { idx } }));
        markDirty(true);
      };
    });
  }
}

/* ---------------- <spark-mem-row> 记忆行 ---------------- */
class SparkMemRow extends HTMLElement {
  connectedCallback() {
    mountTpl(this, "tpl-mem-row");
    this.mk = this.querySelector(".mk");
    this.mv = this.querySelector(".mv");
    this.md = this.querySelector(".md");
    this.editBtn = this.querySelector('[data-edit]');
    this.delBtn = this.querySelector('[data-id]');
    finishMount(this);
  }
  setData(it) {
    deferUntilMounted(this, () => {
      this.mk.textContent = it.key;
      this.mv.textContent = it.value;
      this.md.textContent = fmtTime(it.created_at);
      this.editBtn.dataset.id = it.id;
      this.delBtn.dataset.id = it.id;
    });
  }
}

/* ---------------- <spark-git-row> 检查点行 ---------------- */
class SparkGitRow extends HTMLElement {
  connectedCallback() {
    mountTpl(this, "tpl-git-row");
    this.gh = this.querySelector(".gh");
    this.gt = this.querySelector(".gt");
    this.gm = this.querySelector(".gm");
    finishMount(this);
  }
  setData(c, onReset) {
    deferUntilMounted(this, () => {
      this.gh.textContent = c.hash;
      this.gt.textContent = c.time;
      this.gm.textContent = c.message;
      const b = this.querySelector(".del");
      b.onclick = () => onReset && onReset(c);
    });
  }
}

/* 注册表（元素名 → 类） */
const SPARK_COMPONENTS = [
  ["spark-msg", SparkMsg],
  ["spark-tool-card", SparkToolCard],
  ["spark-plan-card", SparkPlanCard],
  ["spark-think", SparkThink],
  ["spark-session-card", SparkSessionCard],
  ["spark-mcp-row", SparkMcpRow],
  ["spark-mem-row", SparkMemRow],
  ["spark-git-row", SparkGitRow],
];
for (const [tag, cls] of SPARK_COMPONENTS) {
  if (!customElements.get(tag)) customElements.define(tag, cls);
}

export { SPARK_COMPONENTS };
