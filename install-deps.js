/**
 * Post-install script: ensure Python dependencies are installed.
 */

const { spawnSync } = require('child_process');
const path = require('path');

const requirements = path.join(__dirname, 'requirements.txt');
const python = process.env.PYTHON_PATH || 'python3';

console.log('Checking Python dependencies for Google Ads Library MCP...');

const result = spawnSync(python, ['-m', 'pip', 'install', '-q', '-r', requirements], {
  stdio: 'inherit',
  env: process.env,
});

if (result.error) {
  console.warn('Warning: Could not auto-install Python dependencies.');
  console.warn('Please run manually: pip install -r requirements.txt');
} else {
  console.log('Python dependencies ready.');
}
