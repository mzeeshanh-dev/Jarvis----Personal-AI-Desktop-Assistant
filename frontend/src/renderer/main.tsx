import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';
import { App } from './App';
import { FloatView } from './views/FloatView';
import { StoreProvider } from './lib/store';

const isFloat = window.location.hash.startsWith('#/float');
if (isFloat) document.body.classList.add('float');

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <StoreProvider role={isFloat ? 'float' : 'main'}>
      {isFloat ? <FloatView /> : <App />}
    </StoreProvider>
  </StrictMode>,
);
