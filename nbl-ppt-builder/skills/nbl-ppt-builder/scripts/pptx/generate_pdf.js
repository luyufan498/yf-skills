const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');
const url = require('url');

async function generatePdf(htmlFilePath, pdfOutputPath) {
  console.log('📊 NBL PPT Builder - PDF 生成模式\n');

  // 检查 HTML 文件是否存在
  if (!fs.existsSync(htmlFilePath)) {
    throw new Error(`HTML 文件不存在: ${htmlFilePath}`);
  }

  const browser = await chromium.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox']
  });

  try {
    const page = await browser.newPage();

    console.log(`📄 正在加载 HTML 文件: ${htmlFilePath}`);
    await page.goto(url.pathToFileURL(htmlFilePath).href, { waitUntil: 'networkidle', timeout: 60000 });

    // 修复：确保 html/body 高度随内容展开，否则 Chromium 打印引擎会将内容压缩到一页
    // 同时强制覆盖 @page 规则，确保 16:9 输出
    await page.addStyleTag({
      content: `
        @page { size: 960px 540px; margin: 0; }
        html { height: auto !important; min-height: auto !important; }
        body { height: auto !important; min-height: auto !important; width: auto !important; }
      `
    });

    // 配置 PDF 选项 - 使用自定义尺寸匹配 16:9 幻灯片格式
    const pdfOptions = {
      path: pdfOutputPath,
      width: '960px',    // 幻灯片宽度
      height: '540px',   // 幻灯片高度 (16:9)
      preferCSSPageSize: false,  // 使用下方指定的 width/height，不优先采用 CSS @page 规则
      printBackground: true,
      scale: 1,
      margin: {
        top: '0cm',
        right: '0cm',
        bottom: '0cm',
        left: '0cm'
      }
    };

    // 检测页面数量
    const pageCount = await page.evaluate(() => document.querySelectorAll('.page').length);
    console.log(`📄 检测到 ${pageCount} 个幻灯片页面`);

    if (pageCount === 0) {
      throw new Error('未检测到任何幻灯片页面，请检查 HTML 文件是否正确');
    } else if (pageCount === 1) {
      console.log('⚠️  警告：只检测到 1 个页面。PDF 将只输出 1 页。');
      console.log('     如果预期有多个页面，请确认传入的是 merged_presentation.html（由 merge_ppt_pages.py 生成的合并文件），而非单个页面文件。');
    }

    console.log(`📐 使用尺寸: 960px × 540px (16:9 格式)`);
    console.log('💾 正在生成 PDF...');

    await page.pdf(pdfOptions);

    console.log(`✨ PDF 已保存到: ${pdfOutputPath}`);

  } catch (error) {
    console.error('❌ PDF 生成失败:', error);
    throw error;
  } finally {
    await browser.close();
  }
}

// 命令行参数
const args = process.argv.slice(2);

if (args.length < 1 || args.includes('--help') || args.includes('-h')) {
  console.log(`
NBL PPT Builder - Generate PDF from merged HTML

用法:
  node generate_pdf.js <merged_html文件路径> [PDF输出路径]

参数:
  <merged_html>    合并后的 HTML 文件路径（由 merge_ppt_pages.py 生成）
  [PDF输出路径]     可选，默认保存到 merged_html 同级目录下的 presentation.pdf

示例:
  node generate_pdf.js /path/to/ppt_主题/merged_presentation.html
  node generate_pdf.js /path/to/ppt_主题/merged_presentation.html /path/to/output.pdf

注意:
  必须已安装 Playwright Chromium（cd scripts && uv run playwright install chromium）
`);
  process.exit(args.length < 1 ? 1 : 0);
}

const htmlFilePath = path.resolve(args[0]);
// 如果提供了输出路径则使用，否则使用默认命名
const pdfOutputPath = args.length > 1 ? path.resolve(args[1]) : path.join(path.dirname(htmlFilePath), 'presentation.pdf');

generatePdf(htmlFilePath, pdfOutputPath).catch(err => {
  console.error(err);
  process.exit(1);
});
