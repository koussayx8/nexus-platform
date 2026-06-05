#!/usr/bin/env node
const fs = require('fs');
const path = require('path');
const target = path.join(__dirname, '../node_modules/isolated-vm/isolated-vm.js');
fs.writeFileSync(target, 'module.exports = {};\n');
console.log('isolated-vm stubbed successfully');
