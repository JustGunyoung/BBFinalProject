// app.js

let glossary = {};

async function loadGlossary() {
    try {
        const res = await fetch('glossary.json');
        glossary = await res.json();
    } catch (e) {
        console.error('Failed to load glossary', e);
    }
}

async function fetchState() {
    try {
        const res = await fetch('/api/state');
        const state = await res.json();
        updateUI(state);
    } catch (e) {
        console.error('Failed to fetch state', e);
        document.getElementById('banner-cache').style.display = 'block';
        document.getElementById('banner-cache').innerText = '⚠️ Error: Unable to contact backend server.';
    }
}

function updateUI(state) {
    if (state.status === 'no_data') {
        document.getElementById('banner-cache').style.display = 'block';
        document.getElementById('banner-cache').innerText = '⚠️ Waiting for first poll...';
        return;
    } else {
        document.getElementById('banner-cache').style.display = 'none';
    }

    const timestamp = state.timestamp * 1000;
    const dateStr = new Date(timestamp).toLocaleTimeString();
    document.getElementById('update-label').innerText = `Last updated: ${dateStr}`;

    const oracleStatus = state.oracle_status?.status;
    document.getElementById('banner-oracle').style.display = (oracleStatus === 'stale' || oracleStatus === 'invalid') ? 'block' : 'none';
    document.getElementById('banner-rpc').style.display = (state.rpc_status === 'error') ? 'block' : 'none';
    
    let hasAbnormal = false;

    const tbody = document.getElementById('quotes-tbody');
    tbody.innerHTML = '';
    
    const recs = state.recommendations || {};
    const buyWinner = recs.buy?.winner;
    const sellWinner = recs.sell?.winner;

    state.pools.forEach(pool => {
        const row = document.createElement('tr');
        const feePercent = (pool.fee / 10000).toFixed(2) + '%';
        
        if (pool.abnormal) hasAbnormal = true;

        const buyClass = (buyWinner === feePercent) ? 'winner-highlight' : '';
        const sellClass = (sellWinner === feePercent) ? 'winner-highlight' : '';

        row.innerHTML = `
            <td><strong>${feePercent}</strong></td>
            <td>${pool.price_weth_per_link?.toFixed(6) || '-'}</td>
            <td class="${buyClass}">${pool.quote_buy_link_out ? (parseFloat(pool.quote_buy_link_out)/1e18).toFixed(4) : 'N/A'}</td>
            <td class="${sellClass}">${pool.quote_sell_weth_out ? (parseFloat(pool.quote_sell_weth_out)/1e18).toFixed(4) : 'N/A'}</td>
            <td>${parseFloat(pool.liquidity).toLocaleString()}</td>
        `;
        tbody.appendChild(row);
    });

    document.getElementById('banner-abnormal').style.display = hasAbnormal ? 'block' : 'none';

    if (recs.buy) {
        document.getElementById('rec-buy-text').innerText = recs.buy.reason || recs.buy.status;
    }
    if (recs.sell) {
        document.getElementById('rec-sell-text').innerText = recs.sell.reason || recs.sell.status;
    }

    if (state.stats) {
        document.getElementById('stats-window').innerText = state.stats.window || '-';
        document.getElementById('stats-snapshots').innerText = state.stats.snapshots || '0';
        
        if (state.stats.buy) {
            document.getElementById('stats-buy-03-win').innerText = (state.stats.buy['0.3%']?.win_rate || 0) + '%';
            document.getElementById('stats-buy-10-win').innerText = (state.stats.buy['1.0%']?.win_rate || 0) + '%';
            document.getElementById('stats-buy-med-adv').innerText = (state.stats.buy['0.3%']?.median_adv || 0) + '%';
        }
        if (state.stats.sell) {
            document.getElementById('stats-sell-03-win').innerText = (state.stats.sell['0.3%']?.win_rate || 0) + '%';
            document.getElementById('stats-sell-10-win').innerText = (state.stats.sell['1.0%']?.win_rate || 0) + '%';
            document.getElementById('stats-sell-med-adv').innerText = (state.stats.sell['0.3%']?.median_adv || 0) + '%';
        }
        document.getElementById('stats-drift').innerText = (state.stats.drift * 100).toFixed(2) + '%';
    }
}

document.body.addEventListener('mouseover', (e) => {
    if (e.target.classList.contains('tooltip-custom')) {
        const term = e.target.getAttribute('data-term');
        const desc = glossary[term] || "No description found.";
        document.getElementById('glossary-section').innerHTML = `<strong>${term.replace('_', ' ').toUpperCase()}:</strong> ${desc}`;
    }
});

loadGlossary().then(() => {
    fetchState();
    setInterval(fetchState, 30000);
});
