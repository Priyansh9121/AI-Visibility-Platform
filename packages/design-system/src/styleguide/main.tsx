import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import '../styles/index.css';
import './styleguide.css';
import { Styleguide } from './Styleguide.js';

const root = document.getElementById('root');
if (!root) throw new Error('#root not found');
createRoot(root).render(
  <StrictMode>
    <Styleguide />
  </StrictMode>,
);
