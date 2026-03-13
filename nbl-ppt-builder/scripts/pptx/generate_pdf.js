const { chromium } = require('playwright');
const path = require('path');

async function generatePdf(htmlFilePath, pdfOutputPath) {
  console.log('📊 NBL PPT Builder - PDF 生成模式\n');

  const browser = await chromium.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox']
  });

  try {
    const page = await browser.newPage();

    console.log(`📄 正在加载 HTML 文件: ${htmlFilePath}`);
    await page.goto(`file://${htmlFilePath}`, { waitUntil: 'networkidle0', timeout: 60000 });

    // 配置 PDF 选项 - 使用自定义尺寸匹配 16:9 幻灯片格式
    const pdfOptions = {
      path: pdfOutputPath,
      width: '960px',    // 幻灯片宽度
      height: '540px',   // 幻灯片高度 (16:9)
      preferCSSPageSize: true,
      printBackground: true,
      scale: 1,
      margin: {
        top: '0cm',
        right: '0cm',
        bottom: '0cm',
        left: '0cm'
      }
    };

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

if (args.length < 1) {
  console.error('用法: node generate_pdf.js <HTML文件路径> [PDF输出路径]');
  process.exit(1);
}

const htmlFilePath = path.resolve(args[0]);
// 如果提供了输出路径则使用，否则使用默认命名
const pdfOutputPath = args.length > 1 ? path.resolve(args[1]) : path.join(path.dirname(htmlFilePath), 'presentation.pdf');

generatePdf(htmlFilePath, pdfOutputPath).catch(err => {
  console.error(err);
  process.exit(1);
});
