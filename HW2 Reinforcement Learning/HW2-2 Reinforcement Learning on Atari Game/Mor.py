import numpy as np
import tensorflow as tf

np.random.seed(1)
tf.set_random_seed(1)


# 这里有三个class：Sumtree Memory DQNPrioritizedReplay，DQN会用到前两个类
class SumTree(object):  # 建立 tree 和 data,因为 SumTree 有特殊的数据结构,所以两者都能用一个一维 np.array 来存储
	data_pointer = 0

	def __init__(self, capacity):  # 建立 SumTree 和各种参数
		self.capacity = capacity  # for all priority values优先级值容量，叶节点数
		self.tree = np.zeros(2 * capacity - 1)  # tree的容量，存储树结构 叶+根
		# [--------------Parent nodes-------------][-------leaves to recode priority-------]
		#             size: capacity - 1                       size: capacity
		self.data = np.zeros(capacity, dtype=object)  # for all transitions 创建一个object类型的数据,存储所有转换关系的数据

	# 相当于只在叶节点中存储transition数据
	# [--------------data frame-------------]
	#             size: capacity

	def add(self, p, data):  # 当有新 sample 时, 添加进 tree 和 data
		# 接收p=max_p,data=transition数据
		tree_idx = self.data_pointer + self.capacity - 1  # python从0开始索引，初始时tree_idx表示第一个叶节点的索引值，样本按叶子结点依次向后排
		self.data[
			self.data_pointer] = data  # 更新data_frame，将data=transition放入到data_pointer索引值处，因为data_pointer不断+1，可以构建转移关系的data_frame
		self.update(tree_idx, p)  # 给出索引值tree_idx和max_p（SumTree.tree这个array类型中的倒数memory_size个值中的最大值），更新tree_frame

		self.data_pointer += 1
		if self.data_pointer >= self.capacity:  # replace when exceed the capacity如果超出设置叶节点数，则重新覆盖，即data_pointer=0
			self.data_pointer = 0

	def update(self, tree_idx, p):  # 在添加数据的时，由于某个叶子节点的优先级数值变化，那么它一系列父节点的数值也会发生变化，用update更新
		# 当 sample 被 train, 有了新的 TD-error, 就在 tree 中更新
		change = p - self.tree[tree_idx]  # 用max_p减去tree_idx索引值对应的优先级值得到改变量
		self.tree[tree_idx] = p  # 将tree_idx索引值对应的优先级值更新为max_p
		# then propagate the change through tree通过树结构传递改变量
		while tree_idx != 0:  # this method is faster than the recursive loop in the reference code (while 循环, 测试要比递归结构运行快)
			# 当索引值（叶节点）非零
			tree_idx = (tree_idx - 1) // 2  # //表示取整除，即返回商的整数部分，父节点的索引
			self.tree[tree_idx] += change  # 父节点处对应的值加入其子节点的改变量

	def get_leaf(self, v):  # 根据选取的 v 抽取样本
		"""
		Tree structure and array storage:
		Tree index:
			 0         -> storing priority sum
			/ \
		  1     2
		 / \   / \
		3   4 5   6    -> storing priority for transitions
		Array type for storing:
		[0,1,2,3,4,5,6]
		"""
		parent_idx = 0
		while True:  # the while loop is faster than the method in the reference code
			cl_idx = 2 * parent_idx + 1  # this leaf's left and right kids 左右节点的索引值设置
			# tree结构我们使用一维数组实现，采取从上往下，从左往右的层次结构进行存储
			cr_idx = cl_idx + 1
			if cl_idx >= len(self.tree):  # reach bottom, end search
				leaf_idx = parent_idx
				break
			else:  # downward search, always search for a higher priority node向下搜索，一直找到最高优先级的节点
				if v <= self.tree[cl_idx]:  # 如果v小于左边优先级则父节点赋值为这个左边节点的优先级，v不变
					parent_idx = cl_idx
				else:
					v -= self.tree[cl_idx]  # 否则将v减去左边优先级的值，父节点赋值为这个右边节点的优先级
					parent_idx = cr_idx

		data_idx = leaf_idx - self.capacity + 1  # 数据的索引=叶子索引-叶节点数+1
		return leaf_idx, self.tree[leaf_idx], self.data[data_idx]  # 返回叶节点索引，此叶节点对应的值，此叶节点对应的转换关系

	@property  # 在我们定义数据库字段类的时候,往往需要对其中的类属性做一些限制,一般用get和set方法来写。
	# 那在python中,利用装饰器能够少写代码,又能优雅的实现想要的限制,减少错误的发生

	# Python内置的@property装饰器就是负责把一个方法变成属性调用的
	# 1、只有@property表示只读。
	# 2、同时有@property和@*.setter表示可读可写。
	# 3、同时有@property和@*.setter和@*.deleter表示可读可写可删除。
	def total_p(self):  # 获取 sum(priorities)
		return self.tree[0]  # the root，即总优先级


class Memory(object):  # stored as ( s, a, r, s_ ) in SumTree
	epsilon = 0.01  # small amount to avoid zero priority防止有0优先级出现
	alpha = 0.6  # [0~1] alpha 是一个决定我们要使用多少 ISweight 的影响, 如果 alpha = 0, 我们就没使用到任何 Importance Sampling.
	beta = 0.4  # importance-sampling, from initial value increasing to 1重要性从0.4到1
	beta_increment_per_sampling = 0.001  # 重要性每次增长0.001
	abs_err_upper = 1.  # clipped abs error

	def __init__(self, capacity):  # 建立 SumTree 和各种参数，capacity就是经验池的容量
		self.tree = SumTree(capacity)

	def store(self, transition):  # 存储数据, 用于将新的经验数据存储到Sumtree中
		max_p = np.max(self.tree.tree[
		               -self.tree.capacity:])  # self.tree.tree=SumTree.tree，self.tree.capacity=SumTree.capacity=memory_size
		# -self.tree.capacity:表示取SumTree.tree这个array类型中的倒数memory_size个值（叶子结点
		# np.max取出最大值
		# abs_err_upper和epsilon ，表明p优先级值的范围在[epsilon,abs_err_upper]之间
		# epsilon是一个很小的正常数使优先级值p=|self.abs_errors|+epsilon在|self.abs_errors|为0时也能被抽取到
		# 对于新来的数据，我们也认为它的优先级与当前树中优先级最大的经验相同。
		if max_p == 0:
			max_p = self.abs_err_upper  # （初始）如果最大的p为0，则将p设置为abs_err_upper=1，否则跳过这一步
		self.tree.add(max_p, transition)  # set the max p for new p，调用SumTree中add函数

	def sample(self, n):  # 抽取n个sample
		b_idx, b_memory, ISWeights = np.empty((n,), dtype=np.int32), np.empty((n, self.tree.data[0].size)), np.empty(
			(n, 1))
		# np.empty返回给定形状和类型的新数组，无需初始化条目。
		# batch列表
		# b_idx是np.empty((n,), dtype=np.int32)
		# b_memory是np.empty((n, self.tree.data[0].size))，n行，
		# 列数为SumTree.data这个数组第0个值(第几个值无所谓，因为每个值都是transition形式存入)的列数
		# ISWeights是np.empty((n, 1))，n行1列
		pri_seg = self.tree.total_p / n  # priority segment 将 p 的总合 除以 batch size, 分成 batch size 那么多区间
		self.beta = np.min([1., self.beta + self.beta_increment_per_sampling])  # beta的值会0.001的增加，且最大为1

		min_prob = np.min(self.tree.tree[-self.tree.capacity:]) / self.tree.total_p
		# 为后面calculate ISweight
		# self.tree.tree=SumTree.tree，self.tree.capacity=SumTree.capacity=memory_size
		# -self.tree.capacity:表示取SumTree.tree这个array类型中的倒数memory_size个值
		# np.min取出最小值，再除以总优先级值，得概率
		for i in range(n):
			a, b = pri_seg * i, pri_seg * (i + 1)  # 决定第i个样本的抽取区间
			v = np.random.uniform(a, b)
			idx, p, data = self.tree.get_leaf(
				v)  # return 叶节点索引leaf_idx, 叶节点对应的值self.tree[leaf_idx], 叶节点对应的转换关系self.data[data_idx]
			prob = p / self.tree.total_p  # 用这个对应的优先级值/总优先级值，得到概率，这里并没有论文为代码中的指数α
			ISWeights[i, 0] = np.power(prob / min_prob, -self.beta)  # ISWeights是np.empty((n, 1))，n行1列，这里记录了第i个样本对应的值

			# ISWeight = (N*Pj)^(-beta) / maxi_wi 里面的 maxi_wi 是为了 normalize ISWeight,
			# 所以我们先把他放在一边. 所以单纯的 importance sampling 就是 (N*Pj)^(-beta),
			# 那 maxi_wi = maxi[(N*Pi)^(-beta)].

			# 将这两个式子合并,ISWeight = (N*Pj)^(-beta) / maxi[ (N*Pi)^(-beta) ]。
			# 如果将 maxi[ (N*Pi)^(-beta) ] 中的 (-beta) 提出来, 这就变成了 mini[ (N*Pi) ] ^ (-beta)
			# 有的东西可以抵消掉的. 最后ISWeight = (Pj / mini[Pi])^(-beta)
			b_idx[i], b_memory[i, :] = idx, data
		return b_idx, b_memory, ISWeights

	def batch_update(self, tree_idx, abs_errors):  # 更新树中权重
		# train 完被抽取的 samples 后更新在 tree 中的 sample 的 priority
		abs_errors += self.epsilon  # convert to abs and avoid 0
		clipped_errors = np.minimum(abs_errors, self.abs_err_upper)  # abs_errors与1比较
		ps = np.power(clipped_errors, self.alpha)  # np.power()对clipped_errors求self.alpha次方
		for ti, p in zip(tree_idx, ps):
			self.tree.update(ti, p)  # zip打包为元组，利用update更新tree中优先级值


class DQNPrioritizedReplay:
	def __init__(  # 这里赋初值
			self,
			n_actions,
			n_features,
			learning_rate=0.005,
			reward_decay=0.9,
			e_greedy=0.9,
			replace_target_iter=500,
			memory_size=10000,
			batch_size=32,
			e_greedy_increment=None,
			output_graph=False,
			prioritized=True,
			sess=None,
	):
		self.n_actions = n_actions  # 函数调用有参数的改变，没有则按初值中的值，为方便调用时可以更改参数。反复调试
		self.n_features = n_features
		self.lr = learning_rate
		self.gamma = reward_decay
		self.epsilon_max = e_greedy
		self.replace_target_iter = replace_target_iter  # 隔多少步后将target net 的参数更新为最新的参数
		self.memory_size = memory_size  # 整个记忆库的容量，即RL.store_transition(observation, action, reward, observation_)有多少条
		self.batch_size = batch_size
		self.epsilon_increment = e_greedy_increment  # 表示不断扩大epsilon，以便有更大的概率拿到好的值
		self.epsilon = 0 if e_greedy_increment is not None else self.epsilon_max  # 如果e_greedy_increment没有值，则self.epsilon设置为self.epsilon_max=0.9

		self.prioritized = prioritized  # decide to use prioritized or not

		self.learn_step_counter = 0

		self._build_net()
		t_params = tf.get_collection('target_net_params')  # tf.get_collection(key,scope=None)返回具有给定名称的集合中的值列表
		# 如果未将值添加到该集合，则为空列表。该列表按照收集顺序包含这些值。
		e_params = tf.get_collection('eval_net_params')
		self.replace_target_op = [tf.assign(t, e) for t, e in zip(t_params, e_params)]
		# tf.assign(ref,value,validate_shape=None,use_locking=None,name=None)
		# 该操作在赋值后输出一个张量，该张量保存'ref'的新值。函数完成了将value赋值给ref的作用
		# zip()函数用于将可迭代的对象作为参数，将对象中对应的元素打包成一个个元组，然后返回由这些元组组成的列表。

		if self.prioritized:  # 记忆库存储
			self.memory = Memory(capacity=memory_size)  #
		else:
			self.memory = np.zeros((self.memory_size, n_features * 2 + 2))

		if sess is None:
			self.sess = tf.Session()
			self.sess.run(tf.global_variables_initializer())
		else:
			self.sess = sess

		if output_graph:
			tf.summary.FileWriter("logs/", self.sess.graph)

		self.cost_his = []

	def _build_net(self):  # DQN with Prioritized replay 只多了一个 ISWeights, 这个是算法中提到的 Importance-Sampling Weights,
		# 用来恢复被 Prioritized replay 打乱的抽样概率分布.
		def build_layers(s, c_names, n_l1, w_initializer, b_initializer, trainable):
			with tf.variable_scope('l1'):
				w1 = tf.get_variable('w1', [self.n_features, n_l1], initializer=w_initializer, collections=c_names,
				                     trainable=trainable)
				b1 = tf.get_variable('b1', [1, n_l1], initializer=b_initializer, collections=c_names,
				                     trainable=trainable)
				l1 = tf.nn.relu(tf.matmul(s, w1) + b1)

			with tf.variable_scope('l2'):
				w2 = tf.get_variable('w2', [n_l1, self.n_actions], initializer=w_initializer, collections=c_names,
				                     trainable=trainable)
				b2 = tf.get_variable('b2', [1, self.n_actions], initializer=b_initializer, collections=c_names,
				                     trainable=trainable)
				out = tf.matmul(l1, w2) + b2
			return out

		# ------------------ build evaluate_net ------------------
		self.s = tf.placeholder(tf.float32, [None, self.n_features], name='s')  # input
		self.q_target = tf.placeholder(tf.float32, [None, self.n_actions], name='Q_target')  # for calculating loss
		if self.prioritized:  # 如果用分级记忆，执行下面，否则跳过
			self.ISWeights = tf.placeholder(tf.float32, [None, 1],
			                                name='IS_weights')  # ！！！ self.prioritized 时 eval net 的 input 多加了一个 ISWeights
		# 在通过梯度下降法进行参数更新时，需要加入权重项，因此增加了ISWeigths这一个输入。
		with tf.variable_scope('eval_net'):
			c_names, n_l1, w_initializer, b_initializer = \
				['eval_net_params', tf.GraphKeys.GLOBAL_VARIABLES], 20, \
				tf.random_normal_initializer(0., 0.3), tf.constant_initializer(0.1)  # config of layers设置层的参数

			self.q_eval = build_layers(self.s, c_names, n_l1, w_initializer, b_initializer, True)
		# True表示是否训练
		# Q-network输出是一个向量，表示该状态采取每个动作可以获得的Q值
		with tf.variable_scope('loss'):
			if self.prioritized: :
			# 如果用分级记忆，执行下面
			self.abs_errors = tf.reduce_sum(tf.abs(self.q_target - self.q_eval),
			                                axis=1)  # 表示在axis=1(对于行)维度上进行求和，为更新Sumtree
			self.loss = tf.reduce_mean(self.ISWeights * tf.squared_difference(self.q_target, self.q_eval))  # ！！！这里损失有权重
		else:  # 否则
		self.loss = tf.reduce_mean(tf.squared_difference(self.q_target, self.q_eval))


with tf.variable_scope('train'):
	self._train_op = tf.train.RMSPropOptimizer(self.lr).minimize(self.loss)

# ------------------ build target_net ------------------#都一样
self.s_ = tf.placeholder(tf.float32, [None, self.n_features], name='s_')  # input
with tf.variable_scope('target_net'):
	c_names = ['target_net_params', tf.GraphKeys.GLOBAL_VARIABLES]
	self.q_next = build_layers(self.s_, c_names, n_l1, w_initializer, b_initializer, False)
# 这里是target Q-network，不训练


def store_transition(self, s, a, r, s_):  # 与传统DQN不同之处。因为和 Natural DQN 使用的 Memory 不一样, 所以在存储 transition 的时候方式也不同.
	if self.prioritized:  # prioritized replay
		transition = np.hstack((s, [a, r], s_))  # 就是水平(按列顺序)把数组给堆叠起来
		self.memory.store(transition)  # have high priority for newly arrived transition按Memory中的store存储
	else:  # random replay，传统在于建立一个Q表
		if not hasattr(self, 'memory_counter'):
			self.memory_counter = 0
		transition = np.hstack((s, [a, r], s_))
		index = self.memory_counter % self.memory_size
		self.memory[index, :] = transition
		self.memory_counter += 1


def choose_action(self, observation):
	observation = observation[np.newaxis, :]
	if np.random.uniform() < self.epsilon:
		actions_value = self.sess.run(self.q_eval, feed_dict={self.s: observation})
		action = np.argmax(actions_value)
	else:
		action = np.random.randint(0, self.n_actions)  # np.random.randint用于生成一个指定范围内的整数
	return action


def learn(self):
	if self.learn_step_counter % self.replace_target_iter == 0:  # 每self.replace_target_iter进行一次target网络参数更新
		self.sess.run(self.replace_target_op)
		print('\ntarget_params_replaced\n')

	# 这里抽取样本与原DQN不同
	if self.prioritized:
		tree_idx, batch_memory, ISWeights = self.memory.sample(self.batch_size)  # 按重要程度来抽取，传入要抽取的个数self.batch_size
	else:
		sample_index = np.random.choice(self.memory_size, size=self.batch_size)
		batch_memory = self.memory[sample_index, :]  # 否则为原DQN，无优先级抽取batchsize个样本

	q_next, q_eval = self.sess.run(  # 运行这两个神经网络，正向传播
		[self.q_next, self.q_eval],
		feed_dict={self.s_: batch_memory[:, -self.n_features:],
		           self.s: batch_memory[:, :self.n_features]})  # 将s输入到q-network，s_输入到target q_network

	q_target = q_eval.copy()
	# q_next, q_eval 包含所有 action 的值,而我们需要的只是已经选择好的 action 的值, 其他的并不需要.
	# 所以我们将其他的 action 值全变成 0, 将用到的 action 误差值 反向传递回去, 作为更新凭据.
	# 这是我们最终要达到的样子, 比如 q_target - q_eval = [1, 0, 0] - [-1, 0, 0] = [2, 0, 0]

	# q_eval = [-1, 0, 0] 表示这一个记忆中有我选用过 action 0, 而 action 0 带来的 Q(s, a0) = -1, 所以其他的 Q(s, a1) = Q(s, a2) = 0.
	# q_target = [1, 0, 0] 表示这个记忆中的 r+gamma*maxQ(s_) = 1, 而且不管在 s_ 上我们取了哪个 action,我们都需要对应上 q_eval 中的 action 位置,
	# 所以将 1 放在了 action 0 的位置.

	# 下面也是为了达到上面说的目的, 不过为了更方面让程序运算, 达到目的的过程有点不同.
	# 是将 q_eval 全部赋值给 q_target, 这时 q_target-q_eval 全为 0,
	# 不过 我们再根据 batch_memory 当中的 action 这个 column 来给 q_target 中的对应的 memory-action 位置来修改赋值.
	# 使新的赋值为 reward + gamma * maxQ(s_), 这样 q_target-q_eval 就可以变成我们所需的样子.
	# 具体在下面还有一个举例说明.

	batch_index = np.arange(self.batch_size, dtype=np.int32)  # 返回给定间隔，起始点终止点整数，
	eval_act_index = batch_memory[:, self.n_features].astype(int)
	# 返回一个长度为32的动作列表,从记忆库batch_memory中的标记的第2列，self.n_features=2
	# 即RL.store_transition(observation, action, reward, observation_)中的action
	# 注意从0开始记，observation项列数为self.n_features个，所以eval_act_index得到的是action那一列
	reward = batch_memory[:, self.n_features + 1]

	q_target[batch_index, eval_act_index] = reward + self.gamma * np.max(q_next, axis=1)
	# 前面同DQN
	# 最后我们将这个 (q_target - q_eval) 当成误差, 反向传递会神经网络.所有为 0 的 action 值是当时没有选择的 action, 之前有选择的 action 才有不为0的值.
	# 我们只反向传递之前选择的 action 的值,

	# train eval network
	if self.prioritized:
		_, abs_errors, self.cost = self.sess.run([self._train_op, self.abs_errors, self.loss],
		                                         # 计算这三个值，多了self.abs_errors，在loss上面
		                                         feed_dict={self.s: batch_memory[:, :self.n_features],  # 输入下面三个
		                                                    self.q_target: q_target,
		                                                    self.ISWeights: ISWeights})
		self.memory.batch_update(tree_idx, abs_errors)  # update priority，abs_errors是新的priority
	else:
		_, self.cost = self.sess.run([self._train_op, self.loss],
		                             feed_dict={self.s: batch_memory[:, :self.n_features], self.q_target: q_target})

	self.cost_his.append(self.cost)

	self.epsilon = self.epsilon + self.epsilon_increment if self.epsilon < self.epsilon_max else self.epsilon_max
	self.learn_step_counter += 1