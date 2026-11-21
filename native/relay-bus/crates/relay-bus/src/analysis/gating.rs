// Ported from PLVS (https://github.com/SounDoer/PLVS, MIT, (c) SounDoer), src-tauri/src/dsp/gating.rs, commit abdb5cb.
// See native/relay-bus/THIRD-PARTY-NOTICES.md. Changes: none to the maths; only the module path.

//! BS.1770 / EBU R128 gating math shared by the live meter (`loudness.rs`) and
//! the file-analysis summary meter (`summary_meter.rs`). Pure functions only —
//! any correction here applies to both paths at once.

pub(crate) fn lufs_from_mean_squares(m0: f64, m1: f64) -> f64 {
  let s = m0 + m1;
  if s <= 0.0 {
    f64::NEG_INFINITY
  } else {
    -0.691 + 10.0 * s.log10()
  }
}

struct OrderNode {
  key: f64,
  priority: u64,
  own_count: usize,
  own_sum0: f64,
  own_sum1: f64,
  count: usize,
  sum0: f64,
  sum1: f64,
  left: Option<Box<OrderNode>>,
  right: Option<Box<OrderNode>>,
}

impl OrderNode {
  fn new(key: f64, sum0: f64, sum1: f64) -> Self {
    Self::new_weighted(key, sum0, sum1, 1)
  }

  fn new_weighted(key: f64, sum0: f64, sum1: f64, weight: usize) -> Self {
    let mut priority = key.to_bits().wrapping_add(0x9e3779b97f4a7c15);
    priority = (priority ^ (priority >> 30)).wrapping_mul(0xbf58476d1ce4e5b9);
    priority = (priority ^ (priority >> 27)).wrapping_mul(0x94d049bb133111eb);
    priority ^= priority >> 31;
    Self {
      key,
      priority,
      own_count: weight,
      own_sum0: sum0,
      own_sum1: sum1,
      count: weight,
      sum0,
      sum1,
      left: None,
      right: None,
    }
  }

  fn refresh(&mut self) {
    self.count = self.own_count;
    self.sum0 = self.own_sum0;
    self.sum1 = self.own_sum1;
    for child in [&self.left, &self.right].into_iter().flatten() {
      self.count += child.count;
      self.sum0 += child.sum0;
      self.sum1 += child.sum1;
    }
  }
}

#[derive(Default)]
struct ExactOrderTree {
  root: Option<Box<OrderNode>>,
}

impl ExactOrderTree {
  fn insert(&mut self, key: f64, sum0: f64, sum1: f64) {
    insert_node(&mut self.root, key, sum0, sum1);
  }

  fn insert_weighted(&mut self, key: f64, weight: usize) {
    if weight > 0 {
      insert_weighted_node(&mut self.root, key, weight);
    }
  }

  fn totals(&self) -> (usize, f64, f64) {
    self
      .root
      .as_ref()
      .map(|node| (node.count, node.sum0, node.sum1))
      .unwrap_or((0, 0.0, 0.0))
  }

  fn totals_above(&self, threshold: f64) -> (usize, f64, f64) {
    totals_above(&self.root, threshold)
  }

  fn count_at_or_below(&self, threshold: f64) -> usize {
    let total = self.totals().0;
    total - self.totals_above(threshold).0
  }

  fn kth(&self, index: usize) -> Option<f64> {
    kth(&self.root, index)
  }
}

fn insert_weighted_node(root: &mut Option<Box<OrderNode>>, key: f64, weight: usize) {
  let Some(node) = root.as_mut() else {
    *root = Some(Box::new(OrderNode::new_weighted(key, 0.0, 0.0, weight)));
    return;
  };
  match key.total_cmp(&node.key) {
    std::cmp::Ordering::Equal => node.own_count += weight,
    std::cmp::Ordering::Less => {
      insert_weighted_node(&mut node.left, key, weight);
      if node
        .left
        .as_ref()
        .is_some_and(|left| left.priority < node.priority)
      {
        rotate_right(root);
      }
    }
    std::cmp::Ordering::Greater => {
      insert_weighted_node(&mut node.right, key, weight);
      if node
        .right
        .as_ref()
        .is_some_and(|right| right.priority < node.priority)
      {
        rotate_left(root);
      }
    }
  }
  if let Some(node) = root.as_mut() {
    node.refresh();
  }
}

fn insert_node(root: &mut Option<Box<OrderNode>>, key: f64, sum0: f64, sum1: f64) {
  let Some(node) = root.as_mut() else {
    *root = Some(Box::new(OrderNode::new(key, sum0, sum1)));
    return;
  };
  match key.total_cmp(&node.key) {
    std::cmp::Ordering::Equal => {
      node.own_count += 1;
      node.own_sum0 += sum0;
      node.own_sum1 += sum1;
    }
    std::cmp::Ordering::Less => {
      insert_node(&mut node.left, key, sum0, sum1);
      if node
        .left
        .as_ref()
        .is_some_and(|left| left.priority < node.priority)
      {
        rotate_right(root);
      }
    }
    std::cmp::Ordering::Greater => {
      insert_node(&mut node.right, key, sum0, sum1);
      if node
        .right
        .as_ref()
        .is_some_and(|right| right.priority < node.priority)
      {
        rotate_left(root);
      }
    }
  }
  if let Some(node) = root.as_mut() {
    node.refresh();
  }
}

fn rotate_right(root: &mut Option<Box<OrderNode>>) {
  let mut old_root = root.take().expect("rotation root");
  let mut new_root = old_root.left.take().expect("rotation child");
  old_root.left = new_root.right.take();
  old_root.refresh();
  new_root.right = Some(old_root);
  new_root.refresh();
  *root = Some(new_root);
}

fn rotate_left(root: &mut Option<Box<OrderNode>>) {
  let mut old_root = root.take().expect("rotation root");
  let mut new_root = old_root.right.take().expect("rotation child");
  old_root.right = new_root.left.take();
  old_root.refresh();
  new_root.left = Some(old_root);
  new_root.refresh();
  *root = Some(new_root);
}

fn totals_above(root: &Option<Box<OrderNode>>, threshold: f64) -> (usize, f64, f64) {
  let Some(node) = root else {
    return (0, 0.0, 0.0);
  };
  if node.key <= threshold {
    return totals_above(&node.right, threshold);
  }
  let (left_count, left_sum0, left_sum1) = totals_above(&node.left, threshold);
  let (right_count, right_sum0, right_sum1) = node
    .right
    .as_ref()
    .map(|right| (right.count, right.sum0, right.sum1))
    .unwrap_or((0, 0.0, 0.0));
  (
    left_count + node.own_count + right_count,
    left_sum0 + node.own_sum0 + right_sum0,
    left_sum1 + node.own_sum1 + right_sum1,
  )
}

fn kth(root: &Option<Box<OrderNode>>, index: usize) -> Option<f64> {
  let node = root.as_ref()?;
  let left_count = node.left.as_ref().map(|left| left.count).unwrap_or(0);
  if index < left_count {
    kth(&node.left, index)
  } else if index < left_count + node.own_count {
    Some(node.key)
  } else {
    kth(&node.right, index - left_count - node.own_count)
  }
}

/// Exact session accumulator with logarithmic expected update/query cost and no duration cutoff.
pub(crate) struct IntegratedAccumulator {
  recent: [[f64; 2]; 4],
  recent_head: usize,
  block_count: usize,
  tree: ExactOrderTree,
}

impl IntegratedAccumulator {
  pub(crate) fn new() -> Self {
    Self {
      recent: [[0.0; 2]; 4],
      recent_head: 0,
      block_count: 0,
      tree: ExactOrderTree::default(),
    }
  }

  pub(crate) fn push(&mut self, block: [f64; 2]) {
    self.recent[self.recent_head] = block;
    self.recent_head = (self.recent_head + 1) % 4;
    self.block_count += 1;
    // BS.1770 discards an incomplete gating block, so there is no Integrated before the first
    // complete 400 ms block (EBU Tech 3341 §2.3).
    if self.block_count < 4 {
      return;
    }
    let sum = self
      .recent
      .iter()
      .fold([0.0, 0.0], |acc, item| [acc[0] + item[0], acc[1] + item[1]]);
    let gating_block = [sum[0] / 4.0, sum[1] / 4.0];
    let loudness = lufs_from_mean_squares(gating_block[0], gating_block[1]);
    if loudness > -70.0 {
      self.tree.insert(loudness, gating_block[0], gating_block[1]);
    }
  }

  pub(crate) fn value(&self) -> f64 {
    let (absolute_count, absolute_sum0, absolute_sum1) = self.tree.totals();
    if absolute_count == 0 {
      return f64::NEG_INFINITY;
    }
    let relative_gate = lufs_from_mean_squares(
      absolute_sum0 / absolute_count as f64,
      absolute_sum1 / absolute_count as f64,
    ) - 10.0;
    let (count, sum0, sum1) = self.tree.totals_above(relative_gate);
    if count == 0 {
      f64::NEG_INFINITY
    } else {
      lufs_from_mean_squares(sum0 / count as f64, sum1 / count as f64)
    }
  }
}

pub(crate) struct LraAccumulator {
  tree: ExactOrderTree,
}

/// Exact duration-weighted percentile range without the EBU LRA relative gate.
pub(crate) struct WeightedRangeAccumulator {
  tree: ExactOrderTree,
}

impl WeightedRangeAccumulator {
  pub(crate) fn new() -> Self {
    Self {
      tree: ExactOrderTree::default(),
    }
  }

  pub(crate) fn push(&mut self, loudness: f64, weight: usize) {
    if loudness.is_finite() {
      self.tree.insert_weighted(loudness, weight);
    }
  }

  pub(crate) fn value(&self) -> f64 {
    let count = self.tree.totals().0;
    if count == 0 {
      return f64::NEG_INFINITY;
    }
    let p95 = self
      .tree
      .kth(((count as f64 * 0.95).floor() as usize).min(count - 1))
      .expect("weighted p95 index");
    let p10 = self
      .tree
      .kth((count as f64 * 0.1).floor() as usize)
      .expect("weighted p10 index");
    (p95 - p10).max(0.0)
  }
}

impl LraAccumulator {
  pub(crate) fn new() -> Self {
    Self {
      tree: ExactOrderTree::default(),
    }
  }

  pub(crate) fn push(&mut self, loudness: f64) {
    if loudness.is_finite() && loudness > -70.0 {
      self
        .tree
        .insert(loudness, 10_f64.powf(loudness / 10.0), 0.0);
    }
  }

  pub(crate) fn value(&self) -> f64 {
    let (absolute_count, energy_sum, _) = self.tree.totals();
    if absolute_count < 2 {
      return 0.0;
    }
    let relative_gate = 10.0 * (energy_sum / absolute_count as f64).log10() - 20.0;
    let count = self.tree.totals_above(relative_gate).0;
    if count < 2 {
      return 0.0;
    }
    let offset = self.tree.count_at_or_below(relative_gate);
    let p95 = self
      .tree
      .kth(offset + ((count as f64 * 0.95).floor() as usize).min(count - 1))
      .expect("p95 index");
    let p10 = self
      .tree
      .kth(offset + (count as f64 * 0.1).floor() as usize)
      .expect("p10 index");
    (p95 - p10).max(0.0)
  }
}
