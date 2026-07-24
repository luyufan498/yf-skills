/**
 * NBL PPT Builder - Generate PowerPoint from HTML slides
 *
 * Workflow:
 *   1. Collect all HTML pages (01_*.html, 02_*.html, ...)
 *   2. Create PptxGenJS presentation with 16:9 layout
 *   3. For each HTML page, call html2pptx() to convert to slide
 *   4. Save final .pptx file
 *
 * Usage:
 *   node generate_pptx.js /path/to/ppt/dir [output.pptx]
 *
 * Example:
 *   node generate_pptx.js /home/user/ppt_季度总结_20240131
 */

const path = require('path');
const fs = require('fs');
const pptxgen = require('pptxgenjs');
const html2pptx = require('./html2pptx');
const { html2pptxBatch, launchBrowser } = require('./html2pptx');

// Constants
const SLIDE_WIDTH_IN = 10;
const SLIDE_HEIGHT_IN = 5.625; // 16:9 aspect ratio
const FILE_PATTERN = /^(\d{2,3})([a-z]?)_.*\.html$/;

/**
 * Parse command line arguments
 */
function parseArgs(args) {
  const result = { checkMode: false, workDir: null, htmlFiles: [], outputFile: null };

  for (const arg of args) {
    if (arg === '--check' || arg === '--dry-run') {
      result.checkMode = true;
    } else if (/\.html$/i.test(arg)) {
      result.htmlFiles.push(arg);
    } else if (!result.workDir) {
      result.workDir = arg;
    } else if (!result.outputFile) {
      result.outputFile = arg;
    }
  }

  return result;
}

/**
 * Collect all HTML slide files from directory
 */
function collectSlides(workDir) {
  const files = fs.readdirSync(workDir);
  const slides = [];

  for (const file of files) {
    const match = file.match(FILE_PATTERN);
    if (!match) continue;

    const pageNum = parseInt(match[1], 10);
    const suffix = match[2] || '';  // 字母后缀如 "a", "b"，正常页为空字符串
    const filePath = path.join(workDir, file);

    slides.push({ pageNum, suffix, file, path: filePath });
  }

  // Sort by page number, then by suffix: 03 < 03a < 03b < 04
  slides.sort((a, b) => {
    if (a.pageNum !== b.pageNum) {
      return a.pageNum - b.pageNum;
    }
    return a.suffix.localeCompare(b.suffix);
  });

  return slides;
}

/**
 * Check multiple HTML files（复用 browser 实例）
 */
async function checkFiles(htmlFiles) {
  const fileCount = htmlFiles.length;
  console.log(`\n📊 NBL PPT Builder - ${fileCount === 1 ? '单文件' : '多文件'}检测模式\n`);

  // Validate all files exist
  for (const htmlFile of htmlFiles) {
    if (!fs.existsSync(htmlFile)) {
      throw new Error(`File not found: ${htmlFile}`);
    }
  }

  // Create presentation for validation
  const pptx = new pptxgen();
  pptx.layout = 'LAYOUT_16x9';
  pptx.author = 'NBL PPT Builder';
  pptx.title = 'NBL PPT Validation';

  console.log(`🔍 Checking ${fileCount} file(s) for validation errors...\n`);

  const checkErrors = [];
  const browser = await launchBrowser();

  try {
    for (const htmlFile of htmlFiles) {
      const fileName = path.basename(htmlFile);
      process.stdout.write(`   📄 Checking ${fileName}... `);

      try {
        await html2pptx(htmlFile, pptx, { checkMode: true, browser });
        console.log('✅');
      } catch (error) {
        console.log('❌');
        checkErrors.push({ slide: fileName, error: error.message });
      }
    }
  } finally {
    await browser.close();
  }

  // 输出汇总报告
  const passedCount = fileCount - checkErrors.length;
  console.log(`\n📊 检测汇总报告`);
  console.log(`  检测文件总数: ${fileCount}`);
  console.log(`  ✅ 通过: ${passedCount}`);
  console.log(`  ❌ 失败: ${checkErrors.length}`);

  if (checkErrors.length === 0) {
    console.log(`\n💡 提示: 所有文件检测通过，可以安全转换`);
  } else {
    console.log(`\n📋 问题详情:`);
    checkErrors.forEach((e, i) => {
      console.log(`\n  ${i + 1}. ${e.slide}`);
      console.log(`     ${e.error}`);
    });
    console.log(`\n💡 提示: 请先修复上述问题后再生成 PPT`);
    throw new Error(`检测失败: ${checkErrors.length} 个文件存在问题`);
  }
}

/**
 * Main generation function（复用 browser 实例）
 */
async function generatePPTX(workDir, outputFile = 'presentation.pptx', checkMode = false) {
  const mode = checkMode ? '检测模式' : '生成模式';
  console.log(`\n📊 NBL PPT Builder - ${mode}\n`);

  // Validate work directory
  if (!fs.existsSync(workDir)) {
    throw new Error(`Work directory not found: ${workDir}`);
  }

  const slides = collectSlides(workDir);
  if (slides.length === 0) {
    throw new Error(`No slide files found in ${workDir}. Files must match pattern: 01_*.html`);
  }

  console.log(`📁 Found ${slides.length} slide(s):`);
  slides.forEach((slide, idx) => {
    console.log(`   ${idx + 1}. ${slide.file}`);
  });

  if (checkMode) {
    console.log(`\n🔍 Checking slides for validation errors...`);
  } else {
    console.log(`\n🔄 Converting slides to PowerPoint...`);
  }

  // Create presentation (even in check mode, needed for validation)
  const pptx = new pptxgen();
  pptx.layout = 'LAYOUT_16x9';
  pptx.author = 'NBL PPT Builder';
  pptx.title = path.basename(workDir);

  const checkErrors = [];
  const browser = await launchBrowser();

  try {
    // Convert each slide（复用 browser）
    for (const slide of slides) {
      process.stdout.write(`   📄 ${checkMode ? 'Checking' : 'Converting'} ${slide.file}... `);

      try {
        await html2pptx(slide.path, pptx, { checkMode, browser });
        console.log('✅');
      } catch (error) {
        console.log('❌');
        if (checkMode) {
          checkErrors.push({ slide: slide.file, error: error.message });
        } else {
          throw new Error(`Failed to convert ${slide.file}: ${error.message}`);
        }
      }
    }
  } finally {
    await browser.close();
  }

  if (checkMode) {
    // 输出汇总报告
    const passedCount = slides.length - checkErrors.length;
    console.log(`\n📊 检测汇总报告`);
    console.log(`  检测文件总数: ${slides.length}`);
    console.log(`  ✅ 通过: ${passedCount}`);
    console.log(`  ❌ 失败: ${checkErrors.length}`);

    if (checkErrors.length === 0) {
      console.log(`\n💡 提示: 所有文件检测通过，使用以下命令生成 PPT:`);
      console.log(`   node generate_pptx.js "${workDir}" "${outputFile}"`);
    } else {
      console.log(`\n📋 问题详情:`);
      checkErrors.forEach((e, i) => {
        console.log(`\n  ${i + 1}. ${e.slide}`);
        console.log(`     ${e.error}`);
      });
      console.log(`\n💡 提示: 请先修复上述问题后再生成 PPT`);
      throw new Error(`检测失败: ${checkErrors.length} 个文件存在问题`);
    }
  } else {
    console.log(`\n💾 Saving to ${outputFile}...`);
    await pptx.writeFile({ fileName: outputFile });

    console.log(`\n✨ Done! ${slides.length} slides saved to ${outputFile}\n`);
  }

  return checkMode ? null : outputFile;
}

// CLI interface
const args = process.argv.slice(2);

if (args.length < 1 || args.includes('--help') || args.includes('-h')) {
  console.error(`
NBL PPT Builder - Generate PowerPoint from HTML slides

Usage:
  检测模式（推荐先执行）:
    node generate_pptx.js --check <work_dir>

  检测多个指定页面:
    node generate_pptx.js --check <page1.html> [page2.html] [page3.html]

  生成模式（检测通过后执行）:
    node generate_pptx.js <work_dir> [output_file]

Options:
  --check, --dry-run    检测模式：只检查不生成，报告所有问题
  --help, -h           显示帮助信息

Examples:
  1. 检测所有页面:
     node generate_pptx.js --check /path/to/ppt_季度总结_20240131

  2. 检测单个页面:
     node generate_pptx.js --check /path/to/ppt_季度总结_20240131/03_背景介绍.html

  3. 检测多个指定页面:
     node generate_pptx.js --check 03_背景.html 04_问题.html 05_方案.html

  4. 生成 PPT 文件:
     node generate_pptx.js /path/to/ppt_季度总结_20240131

  5. 生成并指定输出文件名:
     node generate_pptx.js /path/to/ppt_季度总结_20240131 quarterly_report.pptx
  `);
  process.exit(1);
}

const { checkMode, workDir, htmlFiles, outputFile } = parseArgs(args);

// 互斥校验：不能同时指定 HTML 文件和工作目录
if (htmlFiles.length > 0 && workDir) {
  console.error(`\n❌ 参数错误：不能同时指定 HTML 文件和工作目录\n`);
  console.error(`  HTML 文件: ${htmlFiles.join(', ')}`);
  console.error(`  工作目录: ${workDir}\n`);
  process.exit(1);
}

// File check mode (single or multiple)
if (htmlFiles.length > 0 && !workDir) {
  checkFiles(htmlFiles)
    .then(() => process.exit(0))
    .catch(error => {
      console.error(`\n❌ Error: ${error.message}\n`);
      process.exit(1);
    });
} else {
  // Directory mode (check or generate)
  // 输出路径警告：未显式指定时默认保存到当前工作目录（cwd），而非 workDir
  const finalOutput = outputFile || 'presentation.pptx';
  if (!outputFile && !checkMode) {
    console.log(`\n💡 提示: 未指定输出文件名，默认保存到当前工作目录: ${path.resolve(finalOutput)}`);
    console.log(`   如需保存到 workDir，请显式指定: node generate_pptx.js "${workDir}" "${path.join(workDir, 'presentation.pptx')}"\n`);
  }
  generatePPTX(workDir, finalOutput, checkMode)
    .then(() => process.exit(0))
    .catch(error => {
      console.error(`\n❌ Error: ${error.message}\n`);
      process.exit(1);
    });
}

module.exports = { generatePPTX, collectSlides };