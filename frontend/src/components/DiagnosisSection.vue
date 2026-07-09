<template>
  <section class="workspace-section diagnosis-section" aria-labelledby="diagnosis-title">
    <div class="section-header">
      <p class="section-eyebrow">Diagnosis</p>
      <h2 id="diagnosis-title">错误研判区</h2>
    </div>

    <label class="field-label" for="error-input">输入错误区</label>
    <textarea
      id="error-input"
      class="error-input"
      name="error-input"
      placeholder="按格式输入错误"
    ></textarea>

    <div class="diagnosis-controls">
      <button class="primary-action" type="button">错误研判</button>
    </div>

    <p class="result-label">错误研判结果</p>
    <output class="result-box" aria-live="polite"></output>
  </section>
</template>
